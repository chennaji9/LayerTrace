# Caddy 跨主机 HTTPS 入口

## 职责边界

Caddy **只**承担：

1. 控制面跨主机入站 HTTPS 终止（TLS 1.2+）；
2. HTTP 明文请求拒绝（80 端口仅重定向到 HTTPS）；
3. 向控制面传递 `X-Forwarded-Proto` / `X-Forwarded-Host`。

控制面的 HTTP 端口仅绑定 loopback（`127.0.0.1:8000`），同一主机的开发与回放验证
不经过 Caddy。跨主机浏览器和探针使用受信HTTPS，也可使用README中的SSH回环隧道。
当前交付不启用remote-write接收端；不能将此Caddy入口理解为已配置指标写入接口。

Compose控制面侧配置 `TRUSTED_PROXIES=172.31.255.0/24`（专用网段），部署可继续收紧到Caddy实际地址。
直接运行时默认仅信任loopback。只信任配置地址的转发协议头，其余来源伪造的 `X-Forwarded-*` 一律忽略（见
`apps/control_plane/middleware/transport_security.py`）。

## 证书签发与挂载

证书不入仓库。将服务端证书与私钥放入本目录：

```
deploy/caddy/certs/tls.crt
deploy/caddy/certs/tls.key
```

可用内部 CA 签发（示例）：

```bash
# CA（一次性）
openssl req -x509 -newkey rsa:3072 -nodes -keyout ca.key -out ca.crt -days 3650 \
  -subj "/CN=LayerTrace Internal CA"

# 服务端证书（SAN 必须包含访问主机名，例如 layertrace.local）
openssl req -newkey rsa:3072 -nodes -keyout tls.key -out tls.csr \
  -subj "/CN=layertrace.local"
openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out tls.crt -days 397 \
  -extfile <(printf "subjectAltName=DNS:layertrace.local")
```

客户端（浏览器、探针、Prometheus）必须信任该 CA 并校验主机名与有效期；
不可信 CA、主机名不匹配或过期证书的连接会被拒绝（SC-011）。

## 轮换

替换 `certs/tls.crt` / `certs/tls.key` 文件后执行：

```bash
docker compose -f deploy/compose.yaml exec caddy caddy reload --config /etc/caddy/Caddyfile
```

轮换不影响已提交事件（探针有界 spool + 服务端幂等写入保证补传，见 SC-008）。

## 撤销

内部 CA 场景使用短有效期（≤397 天）+ 主动轮换代替 CRL；撤销即从客户端信任列表
移除 CA 并轮换服务端证书。
