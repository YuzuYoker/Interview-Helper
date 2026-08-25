"""Redis 缓存命中测试（轻量版，只测 Redis 连接和缓存操作）。

用法：
    docker compose exec backend python backend/scripts/eval_cache.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.config import settings


def main() -> None:
    settings.init_env()
    
    print("=" * 78)
    print("Redis 缓存测试")
    print("=" * 78)
    print(f"Redis URL: {settings.redis_url or '未配置'}")
    print(f"缓存 TTL: {settings.cache_ttl}s")
    print()

    if not settings.redis_url:
        print("⚠ Redis 未配置，请在 .env 中设置 REDIS_URL")
        print("  Docker 环境: REDIS_URL=redis://redis:6379/0")
        print("  本地环境: REDIS_URL=redis://localhost:6379/0")
        return

    # 测试 Redis 连接
    print("阶段 1: 测试 Redis 连接")
    print("-" * 78)
    try:
        import redis
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        t0 = time.monotonic()
        pong = client.ping()
        latency_ms = (time.monotonic() - t0) * 1000
        print(f"  ✓ 连接成功: PONG ({latency_ms:.1f}ms)")
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        return

    # 测试基本缓存操作
    print("\n阶段 2: 测试 SET/GET 操作")
    print("-" * 78)
    try:
        # SET
        t0 = time.monotonic()
        client.set("test:cache:key", "test_value", ex=60)
        set_ms = (time.monotonic() - t0) * 1000
        print(f"  ✓ SET: {set_ms:.1f}ms")

        # GET
        t0 = time.monotonic()
        value = client.get("test:cache:key")
        get_ms = (time.monotonic() - t0) * 1000
        print(f"  ✓ GET: {get_ms:.1f}ms (value={value!r})")

        # 批量测试
        print("\n阶段 3: 批量性能测试（100 次 SET+GET）")
        print("-" * 78)
        set_times = []
        get_times = []
        for i in range(100):
            key = f"test:batch:{i}"
            value = f"test_value_{i}" * 100  # 模拟较大 payload
            
            t0 = time.monotonic()
            client.setex(key, 60, value)
            set_times.append((time.monotonic() - t0) * 1000)
            
            t0 = time.monotonic()
            result = client.get(key)
            get_times.append((time.monotonic() - t0) * 1000)
            
            assert result == value, f"值不匹配: {result[:20]}... != {value[:20]}..."
        
        print(f"  SET: 平均 {sum(set_times)/len(set_times):.2f}ms, "
              f"最小 {min(set_times):.2f}ms, 最大 {max(set_times):.2f}ms")
        print(f"  GET: 平均 {sum(get_times)/len(get_times):.2f}ms, "
              f"最小 {min(get_times):.2f}ms, 最大 {max(get_times):.2f}ms")
        
        # 清理测试数据
        for i in range(100):
            client.delete(f"test:batch:{i}")
        client.delete("test:cache:key")
        
    except Exception as e:
        print(f"  ✗ 操作失败: {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n" + "=" * 78)
    print("Redis 缓存测试完成 ✓")
    print("=" * 78)


if __name__ == "__main__":
    main()