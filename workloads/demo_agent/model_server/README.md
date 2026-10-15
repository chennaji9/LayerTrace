# 本地模型服务（离线）

按 `manifest.yaml` 固定模型与 llama.cpp 版本；下载后回填 SHA-256。
启动示例（llama.cpp release 二进制）：

```bash
set -a; source server.env; set +a
llama-server -m $LLAMA_MODEL --host $LLAMA_HOST --port $LLAMA_PORT \
  -c $LLAMA_CTX_SIZE -t $LLAMA_THREADS
```

仅监听 loopback；工作负载通过本地址调用，不产生外网连接。
