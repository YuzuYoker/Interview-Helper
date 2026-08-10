"""检索效果对比实验（面试可讲的数据）。

对比四配置：①纯向量 ②BM25+RRF ③+Reranker ④+HyDE（实验）
指标：Hit@1/3/5、MRR、平均检索时延；多轮改写测试。

用法（需先停止 uvicorn，Qdrant local 单实例）：
    python backend/scripts/eval_retrieval.py
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.retriever import retrieve
from app.utils.config import ROOT_DIR, settings

EVAL_DIR = ROOT_DIR / "backend" / "data" / "eval"
QUESTIONS_CACHE = EVAL_DIR / "questions.json"

# 人工 golden：真实面试风格，覆盖主要参考文献（诚实基线）
GOLDEN = [
    {"question": "行为面试怎么准备？STAR法则的四个要素是什么？", "expected_source": "behavioral_question_bank.md"},
    {"question": "技术面试一般考察哪些能力？怎么准备算法题？", "expected_source": "role_technical.md"},
    {"question": "怎么评估自己的议价能力？谈薪话术有哪些？", "expected_source": "salary_negotiation.md"},
    {"question": "面试完怎么复盘？复盘应该分几个层面？", "expected_source": "interview_evaluation_criteria.md"},
    {"question": "Case Study 面试怎么准备？分析框架有哪些？", "expected_source": "case_study_guide.md"},
    {"question": "面试官问'你还有什么问题吗'，反问什么比较好？", "expected_source": "reverse_questions_bank.md"},
    {"question": "公司调研要覆盖哪些维度？", "expected_source": "company_research_guide.md"},
    {"question": "AI产品经理面试和普通产品面试有什么不同？", "expected_source": "role_ai_product.md"},
    {"question": "简历上项目经历怎么写才突出？", "expected_source": "resume_analysis_checklist.md"},
    {"question": "多轮面试各轮次考察重点是什么？怎么安排？", "expected_source": "multi_round_strategy.md"},
    {"question": "压力面试怎么应对？", "expected_source": "interview_advanced_guide.md"},
    {"question": "拿到多个offer怎么对比选择？", "expected_source": "offer_evaluation.md"},
]

# 多轮对话测试：第二轮含指代/省略，验证改写生效
MULTI_TURN = [
    (
        [
            {"role": "user", "content": "怎么准备谈薪？"},
            {"role": "assistant", "content": "先做市场行情调研，再评估自己的议价能力。"},
        ],
        "那公司压价怎么办？",
        "salary_negotiation.md",
    ),
    (
        [
            {"role": "user", "content": "技术面一般问什么？"},
            {"role": "assistant", "content": "数据结构、系统设计、项目深挖等。"},
        ],
        "那算法题怎么准备？",
        "role_technical.md",
    ),
    (
        [
            {"role": "user", "content": "面试之后多久没消息算凉了？"},
            {"role": "assistant", "content": "一般一周左右。"},
        ],
        "凉了的话怎么复盘？",
        "interview_evaluation_criteria.md",
    ),
    (
        [
            {"role": "user", "content": "准备投AI产品经理的岗位"},
            {"role": "assistant", "content": "这个岗位对模型理解要求较高。"},
        ],
        "和普通PM比，面试侧重有什么不同？",
        "role_ai_product.md",
    ),
    (
        [
            {"role": "user", "content": "怎么了解目标公司？"},
            {"role": "assistant", "content": "从官网、财报、员工评价等渠道。"},
        ],
        "具体要调研哪些维度？",
        "company_research_guide.md",
    ),
]

CONFIGS = [
    ("纯向量（第1周基线）", dict(enable_bm25=False, enable_reranker=False, hyde_enabled=False)),
    ("BM25+RRF", dict(enable_bm25=True, enable_reranker=False, hyde_enabled=False)),
    ("BM25+RRF+Reranker", dict(enable_bm25=True, enable_reranker=True, hyde_enabled=False)),
    ("+HyDE（实验）", dict(enable_bm25=True, enable_reranker=True, hyde_enabled=True)),
]

TOP_KS = [1, 3, 5]


def build_eval_set() -> list[dict]:
    """评估集 = LLM 每篇参考文献生成 2 题（缓存）+ 人工 golden。失败降级只用 golden。"""
    questions = list(GOLDEN)
    if QUESTIONS_CACHE.exists():
        try:
            questions += json.loads(QUESTIONS_CACHE.read_text(encoding="utf-8"))
            print(f"[eval-set] 使用缓存: {QUESTIONS_CACHE.name}（{len(questions)} 题）")
            return questions
        except Exception:
            pass
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        refs = sorted(
            p
            for p in (
                ROOT_DIR / ".claude" / "skills" / "interview-master" / "references"
            ).iterdir()
            if p.suffix == ".md"
        )
        llm = ChatOpenAI(
            model=settings.model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=0.7,
            max_tokens=2000,
            timeout=60,
        )
        generated = []
        for ref in refs:
            resp = llm.invoke(
                [
                    SystemMessage(
                        "你是面试评估集构造器。根据下面的参考资料内容，生成 2 个"
                        "求职者会问的、且能从该资料中找到答案的问题。只输出 JSON 数组，如 [\"q1\",\"q2\"]。"
                    ),
                    HumanMessage(f"资料文件名: {ref.name}\n资料内容(前1000字):\n{ref.read_text(encoding='utf-8')[:1000]}"),
                ]
            )
            content = (resp.content or "").strip()
            if content.startswith("```"):  # 模型可能输出 markdown 代码块
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            qs = json.loads(content)
            generated += [
                {"question": q, "expected_source": ref.name} for q in qs[:2]
            ]
        if generated:
            EVAL_DIR.mkdir(parents=True, exist_ok=True)
            QUESTIONS_CACHE.write_text(
                json.dumps(generated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            questions += generated
            print(f"[eval-set] LLM 生成 {len(generated)} 题并缓存")
    except Exception as e:
        print(f"[eval-set] LLM 生成失败，仅用 golden: {e}")
    return questions


def run_config(name: str, overrides: dict, questions: list[dict]) -> dict:
    for k, v in overrides.items():
        setattr(settings, k, v)

    results = []
    latencies = []
    for q in questions:
        t0 = time.time()
        hits = retrieve(q["question"], k=5)
        latencies.append(time.time() - t0)
        sources = [d.metadata.get("source", "") for d, _ in hits]
        results.append((q["expected_source"], sources))

    stats = {"name": name}
    n = len(results)
    for k in TOP_KS:
        stats[f"hit@{k}"] = (
            sum(1 for exp, srcs in results if exp in srcs[:k]) / n
        )
    mrr_sum = 0.0
    for exp, srcs in results:
        for i, s in enumerate(srcs, start=1):
            if s == exp:
                mrr_sum += 1.0 / i
                break
    stats["mrr"] = mrr_sum / n
    stats["avg_latency"] = sum(latencies) / n
    return stats


def run_multi_turn() -> list[dict]:
    """多轮改写测试：第二轮含指代/省略，验证改写后仍命中期望来源。"""
    for k, v in dict(enable_bm25=True, enable_reranker=True, hyde_enabled=False).items():
        setattr(settings, k, v)
    out = []
    for history, question, expected in MULTI_TURN:
        hits = retrieve(question, k=5, history=history)
        sources = [d.metadata.get("source", "") for d, _ in hits]
        out.append(
            {
                "question": question,
                "expected": expected,
                "hit": expected in sources,
                "top_sources": sources[:3],
            }
        )
    return out


def main() -> None:
    settings.init_env()
    if not settings.deepseek_api_key:
        print("警告: DEEPSEEK_API_KEY 未配置，LLM 生成/HyDE 将不可用")

    questions = build_eval_set()
    print(f"\n评估集: {len(questions)} 题（golden {len(GOLDEN)} + LLM {len(questions) - len(GOLDEN)}）\n")

    print("=" * 78)
    print(f"{'配置':<22} {'Hit@1':>7} {'Hit@3':>7} {'Hit@5':>7} {'MRR':>7} {'平均时延':>10}")
    print("=" * 78)
    rows = []
    for name, overrides in CONFIGS:
        stats = run_config(name, overrides, questions)
        rows.append(stats)
        print(
            f"{stats['name']:<22} {stats['hit@1']:>7.1%} {stats['hit@3']:>7.1%} "
            f"{stats['hit@5']:>7.1%} {stats['mrr']:>7.3f} {stats['avg_latency']:>8.2f}s"
        )
    print("=" * 78)

    print("\n多轮改写测试（指代/省略场景）:")
    mt = run_multi_turn()
    for m in mt:
        mark = "✓" if m["hit"] else "✗"
        print(f"  {mark} 「{m['question']}」→ 期望 {m['expected']}, 命中: {m['top_sources']}")
    mt_hit = sum(1 for m in mt if m["hit"]) / len(mt)
    print(f"  改写命中率: {mt_hit:.0%}")

    # 输出报告
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    report = EVAL_DIR / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# 检索效果对比实验报告",
        f"\n生成时间: {datetime.now().isoformat()}",
        f"评估集: {len(questions)} 题（golden {len(GOLDEN)} + LLM {len(questions) - len(GOLDEN)}）",
        f"知识库: {settings.qdrant_collection}（{settings.embedding_model} / {settings.reranker_model}）",
        "\n| 配置 | Hit@1 | Hit@3 | Hit@5 | MRR | 平均时延 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['hit@1']:.1%} | {r['hit@3']:.1%} | "
            f"{r['hit@5']:.1%} | {r['mrr']:.3f} | {r['avg_latency']:.2f}s |"
        )
    lines.append(f"\n多轮改写命中率: {mt_hit:.0%}\n")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已保存: {report}")


if __name__ == "__main__":
    main()
