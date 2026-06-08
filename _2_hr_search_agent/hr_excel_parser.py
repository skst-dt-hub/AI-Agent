import pandas as pd
import json
import sys
from pathlib import Path


def clean_cell(value):
    """엑셀 셀 값을 비교와 JSON 저장에 맞게 문자열로 정리한다."""
    if pd.isna(value):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if text.endswith(".0"):
        number_part = text[:-2]
        if number_part.replace("-", "", 1).isdigit():
            return number_part
    return text


def is_person_number(value):
    text = clean_cell(value)
    if not text:
        return False
    try:
        return float(text).is_integer()
    except ValueError:
        return False


def parse_hr_excel(file_path: str):
    """
    HR 데이터 엑셀 파일을 파싱해서
    structured_data.json과 text_data.json으로 변환하는 스크립트.

    엑셀 구조:
    - 1열: 대분류 (현재업무, 사내경력 등)
    - 2열: 소분류 (직무명, 주요업무 등)
    - 3열: 세부분류 (과제명, 배경/과제 등) - 없을 수도 있음
    - 4열~: 사람별 데이터 (No.1, No.2, ...)
    """

    df = pd.read_excel(file_path, header=None, dtype=object)

    # 사람 수 파악: "No." 셀을 전체 시트에서 찾고, 그 오른쪽 숫자 열을 사람 데이터로 본다.
    # 템플릿 앞쪽에 빈 열이 추가되어도 동작하도록 첫 번째 열에 고정하지 않는다.
    no_row_idx = None
    no_col_idx = None
    for i, row in df.iterrows():
        for col_idx, value in row.items():
            if clean_cell(value) == "No.":
                no_row_idx = i
                no_col_idx = col_idx
                break
        if no_row_idx is not None:
            break

    if no_row_idx is None:
        raise ValueError("'No.' 행을 찾을 수 없어요.")

    no_row = df.iloc[no_row_idx]
    person_cols = []
    for col_idx in range(no_col_idx + 1, len(no_row)):
        if is_person_number(no_row[col_idx]):
            person_cols.append(col_idx)

    if not person_cols:
        raise ValueError("'No.' 행 오른쪽에서 사람 번호 열을 찾을 수 없어요.")

    label_cols = list(range(no_col_idx, person_cols[0]))
    while len(label_cols) < 3:
        label_cols.append(None)

    print(f"총 {len(person_cols)}명 발견 (열 인덱스: {person_cols})")

    # 사번 행 찾기 (사람 ID로 사용)
    sabun_row_idx = None
    for i, row in df.iterrows():
        if any(clean_cell(value) == "사번" for value in row):
            sabun_row_idx = i
            break

    # 전체 행을 (대분류, 소분류, 세부분류, 값...) 형태로 파싱
    rows_parsed = []
    for i, row in df.iterrows():
        cat1 = clean_cell(row[label_cols[0]]) if label_cols[0] is not None else ""   # 대분류
        cat2 = clean_cell(row[label_cols[1]]) if label_cols[1] is not None else ""   # 소분류
        cat3 = clean_cell(row[label_cols[2]]) if label_cols[2] is not None else ""   # 세부분류
        values = [clean_cell(row[col]) for col in person_cols]
        rows_parsed.append({
            "cat1": cat1,
            "cat2": cat2,
            "cat3": cat3,
            "values": values
        })

    # 사람별로 데이터 조립
    structured_list = []
    text_list = []

    for person_idx in range(len(person_cols)):
        person = {}
        text_parts = []

        current_cat1 = ""
        current_cat2 = ""

        for row in rows_parsed:
            cat1 = row["cat1"] if row["cat1"] not in ("", "nan") else current_cat1
            cat2 = row["cat2"] if row["cat2"] not in ("", "nan") else current_cat2
            cat3 = row["cat3"]
            val  = row["values"][person_idx]

            if cat1 not in ("", "nan"):
                current_cat1 = cat1
            if cat2 not in ("", "nan"):
                current_cat2 = cat2

            if not val or val == "nan":
                continue

            # 키 생성
            if cat3 and cat3 != "nan":
                key = f"{current_cat1}__{current_cat2}__{cat3}"
            elif cat2 and cat2 != "nan":
                key = f"{current_cat1}__{cat2}"
            else:
                key = current_cat1

            person[key] = val

        # ── 정형 데이터 추출 ──────────────────────────────────
        def g(k):
            """키 일부로 값 찾기"""
            for full_key, v in person.items():
                if k in full_key:
                    return v
            return ""

        # 경력연수 계산 (사내경력 시작일 기준)
        career_start = g("사내경력__시작일")
        career_years = 0
        if career_start:
            try:
                from datetime import datetime
                start_year = int(str(career_start)[:4])
                career_years = 2025 - start_year
            except:
                career_years = 0

        # 팀리딩기간 → 숫자 변환
        leading_period = g("리더십__팀리딩기간")
        leading_months = 0
        if leading_period:
            import re
            years  = re.search(r"(\d+)년", leading_period)
            months = re.search(r"(\d+)개월", leading_period)
            leading_months = (int(years.group(1)) * 12 if years else 0) + \
                             (int(months.group(1)) if months else 0)

        structured = {
            "사번":           g("사번"),
            "성명":           g("성명"),
            "소속조직":        g("소속조직"),
            "생년월일":        g("생년월일"),
            "성별":           g("성별"),
            "현주소":          g("현주소"),
            "파견가능여부":     True if g("장기파견가능여부") == "Y" else False,
            "현재직무명":       g("현재업무__직무명"),
            "직무수행시작일":    g("현재업무__직무수행 시작일"),
            "경력연수":        career_years,
            "주요업무1_비중":   g("현재업무1__비중(%)"),
            "주요업무1_난이도":  g("현재업무1__난이도"),
            "주요업무1_대체가능성": g("현재업무1__대체가능성"),
            "주요업무2_비중":   g("현재업무2__비중(%)"),
            "주요업무2_난이도":  g("현재업무2__난이도"),
            "주요업무2_대체가능성": g("현재업무2__대체가능성"),
            "주요업무3_비중":   g("현재업무3__비중(%)"),
            "주요업무3_난이도":  g("현재업무3__난이도"),
            "주요업무3_대체가능성": g("현재업무3__대체가능성"),
            "사내경력_직무명":   g("사내경력__직무명"),
            "사외경력_재직기간": g("사외경력__재직기간"),
            "사외경력_연관성":   g("사외경력__현직무와의 연관성"),
            "팀리딩여부":       True if g("리더십__팀리딩여부") == "Y" else False,
            "팀리딩인원":       g("리더십__팀리딩인원"),
            "팀리딩기간_월":    leading_months,
            "본인강점":        g("리더십__본인강점"),
            "희망직무":        g("커리어__희망직무"),
            "직무이동의향":     g("커리어__직무이동의향"),
            "이동가능시점":     g("커리어__이동가능시점"),
            "국내근무가능지역":  g("커리어__국내근무가능지역"),
            "해외근무가능":     True if g("커리어__해외근무/주재가능") == "Y" else False,
        }
        structured_list.append(structured)

        # ── 비정형 텍스트 조립 ────────────────────────────────
        text_parts = []

        직무내용 = g("현재업무__직무내용")
        if 직무내용:
            text_parts.append(f"현재직무: {g('현재업무__직무명')} / {직무내용}")

        for n in ["1", "2", "3"]:
            업무 = g(f"현재업무{n}__주요업무")
            if 업무:
                text_parts.append(f"주요업무{n}: {업무}")

        for n in ["1", "2", "3"]:
            과제명 = g(f"사내경력__과제명{n}__과제명")
            역할   = g(f"사내경력__과제명{n}__본인역할")
            실행   = g(f"사내경력__과제명{n}__주요 실행")
            성과   = g(f"사내경력__과제명{n}__성과/결과")
            if 과제명:
                text_parts.append(
                    f"과제{n}: {과제명} / 역할: {역할} / 실행: {실행} / 성과: {성과}"
                )

        사외업무 = g("사외경력__주요수행업무")
        if 사외업무:
            text_parts.append(f"사외경력: {g('사외경력__회사명')} {g('사외경력__재직기간')} / {사외업무}")

        강점 = g("리더십__본인강점")
        강점사례 = g("리더십__강점활용사례")
        if 강점:
            text_parts.append(f"강점: {강점} / 활용사례: {강점사례}")

        희망 = g("커리어__희망직무")
        희망이유 = g("커리어__희망직무 상세이유")
        if 희망:
            text_parts.append(f"희망직무: {희망} / 이유: {희망이유}")

        text_list.append({
            "사번": structured["사번"],
            "성명": structured["성명"],
            "텍스트": "\n".join(text_parts)
        })

        print(f"  [{person_idx+1}] {structured['성명']} ({structured['소속조직']}) 파싱 완료")

    return structured_list, text_list


def main():
    if len(sys.argv) < 2:
        print("사용법: python hr_excel_parser.py <엑셀파일경로>")
        print("예시:   python hr_excel_parser.py hr_data.xlsx")
        sys.exit(1)

    file_path = sys.argv[1]
    if not Path(file_path).exists():
        print(f"파일을 찾을 수 없어요: {file_path}")
        sys.exit(1)

    print(f"\n{file_path} 파싱 시작...\n")
    structured_list, text_list = parse_hr_excel(file_path)

    # JSON 저장
    output_dir = Path(file_path).parent

    structured_path = output_dir / "structured_data.json"
    text_path       = output_dir / "text_data.json"

    with open(structured_path, "w", encoding="utf-8") as f:
        json.dump(structured_list, f, ensure_ascii=False, indent=2)

    with open(text_path, "w", encoding="utf-8") as f:
        json.dump(text_list, f, ensure_ascii=False, indent=2)

    print(f"\n변환 완료!")
    print(f"  정형 데이터: {structured_path}")
    print(f"  비정형 데이터: {text_path}")
    print(f"  총 {len(structured_list)}명 처리됨")


if __name__ == "__main__":
    main()
