import argparse
import json
from pathlib import Path

from hr_pipeline.candidate_retrieval import retrieve_candidates
from hr_pipeline.explanation import explain_candidates
from hr_pipeline.query_understanding import understand_query
from hr_pipeline.scoring import score_candidates


RESULT_PATH = Path("result.json")


def run_pipeline(query: str) -> dict:
    understood = understand_query(query)
    retrieved = retrieve_candidates(understood)
    scored = score_candidates(retrieved)
    return explain_candidates(scored)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HR 검색 Lambda 3단계를 로컬에서 순서대로 실행합니다.")
    parser.add_argument("query", help="인사담당자 자연어 질의")
    parser.add_argument(
        "--output",
        default=str(RESULT_PATH),
        help="최종 결과 JSON 저장 경로",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_pipeline(args.query)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {output_path}")


if __name__ == "__main__":
    main()
