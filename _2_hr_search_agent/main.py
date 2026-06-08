import argparse
import asyncio
import json
from pathlib import Path

from strands.multiagent.graph import GraphBuilder

from hr_pipeline.candidate_retrieval import retrieve_candidates
from hr_pipeline.explanation import explain_candidates
from hr_pipeline.query_understanding import understand_query
from hr_pipeline.scoring import score_candidates
from strands_function_nodes import FunctionGraphNode, previous_json_input_builder, query_input_builder


RESULT_PATH = Path("result.json")
EXPLANATION_NODE_ID = "explanation_agent"


def build_graph():
    query_understanding_agent = FunctionGraphNode(
        name="query_understanding_agent",
        function=understand_query,
        input_builder=query_input_builder,
    )
    candidate_retrieval_agent = FunctionGraphNode(
        name="candidate_retrieval_agent",
        function=retrieve_candidates,
        input_builder=previous_json_input_builder,
    )
    scoring_agent = FunctionGraphNode(
        name="scoring_agent",
        function=score_candidates,
        input_builder=previous_json_input_builder,
    )
    explanation_agent = FunctionGraphNode(
        name=EXPLANATION_NODE_ID,
        function=explain_candidates,
        input_builder=previous_json_input_builder,
    )

    builder = GraphBuilder()
    builder.add_node(query_understanding_agent, "query_understanding_agent")
    builder.add_node(candidate_retrieval_agent, "candidate_retrieval_agent")
    builder.add_node(scoring_agent, "scoring_agent")
    builder.add_node(explanation_agent, EXPLANATION_NODE_ID)
    builder.add_edge("query_understanding_agent", "candidate_retrieval_agent")
    builder.add_edge("candidate_retrieval_agent", "scoring_agent")
    builder.add_edge("scoring_agent", EXPLANATION_NODE_ID)
    builder.set_entry_point("query_understanding_agent")
    builder.set_max_node_executions(4)
    return builder.build()


def agent_result_to_dict(agent_result) -> dict:
    text = str(agent_result).strip()
    if not text:
        raise ValueError("최종 AgentResult가 비어 있습니다.")
    return json.loads(text)


async def run_graph(query: str) -> dict:
    graph = build_graph()
    graph_result = await graph.invoke_async(query)
    node_result = graph_result.results.get(EXPLANATION_NODE_ID)
    if node_result is None:
        raise RuntimeError(f"{EXPLANATION_NODE_ID} 결과가 없습니다: {graph_result}")
    if hasattr(node_result.status, "value") and node_result.status.value != "completed":
        raise RuntimeError(f"{EXPLANATION_NODE_ID} 상태가 completed가 아닙니다: {node_result.status}")
    return agent_result_to_dict(node_result.result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strands GraphBuilder로 HR 검색 파이프라인을 실행합니다.")
    parser.add_argument("query", nargs="?", help="인사담당자 자연어 질의")
    parser.add_argument(
        "--output",
        default=str(RESULT_PATH),
        help="최종 결과 JSON 저장 경로",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    query = args.query or input("질의를 입력하세요: ").strip()
    if not query:
        raise SystemExit("질의가 비어 있습니다.")

    result = asyncio.run(run_graph(query))
    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {output_path}")


if __name__ == "__main__":
    main()
