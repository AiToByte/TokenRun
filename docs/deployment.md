# TokenRun 部署指南

---

## 1. 部署方式

| 方式 | 适用场景 | 复杂度 |
|------|----------|--------|
| 本地开发 | 开发调试 | 低 |
| Docker Compose | 生产部署 | 中 |
| Kubernetes | 大规模生产 | 高 |

---

## 2. 本地开发部署

### 2.1 环境要求

- Python 3.10+
- Node.js 18+ (前端)
- Git

### 2.2 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# 2. 创建虚拟环境 (推荐)
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. 安装后端依赖
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 5. 安装前端依赖 (可选)
cd web && npm install && cd ..

# 6. 运行测试验证
python -m pytest tests/ -v
```

### 2.3 启动服务

```bash
# 后端 API
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 前端 (另一个终端)
cd web && npm run dev

# 或使用 CLI 模式
python main.py runfiles/test_mission.yaml
```

---

## 3. Docker Compose 部署

### 3.1 目录结构

```
TokenRun/
├── Dockerfile              # 后端镜像
├── docker-compose.yml      # 编排配置
├── web/Dockerfile          # 前端镜像
├── .env                    # 环境变量
├── runfiles/               # 任务蓝图 (挂载)
├── vault/                  # 技能存储 (挂载)
└── logs/                   # 日志 (挂载)
```

### 3.2 配置

```bash
# 创建 .env 文件
cat > .env << EOF
OPENAI_API_KEY=sk-your-key-here
ACTOR_MODEL=gpt-4o
CRITIC_MODEL=gpt-4o-mini
TOKENRUN_API_KEY=your-api-secret
TOKENRUN_CORS_ORIGINS=http://localhost:3000
EOF
```

### 3.3 启动

```bash
# 构建并启动
docker-compose up --build

# 后台运行
docker-compose up -d

# 查看日志
docker-compose logs -f backend

# 停止
docker-compose down
```

### 3.4 服务访问

| 服务 | URL | 说明 |
|------|-----|------|
| API | http://localhost:8000 | FastAPI 后端 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| 前端 | http://localhost:3000 | Next.js Cockpit |
| 健康检查 | http://localhost:8000/health | 状态检查 |

### 3.5 持久化卷

| 卷 | 容器路径 | 说明 |
|----|----------|------|
| `./logs` | `/app/logs` | SQLite traces + 日志 |
| `./vault` | `/app/vault` | 固化技能存储 |
| `./runfiles` | `/app/runfiles` | 任务蓝图 |
| `./output` | `/app/output` | 输出结果 |

---

## 4. 环境变量参考

### 4.1 LLM 配置

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | 是* | — | 共享 API Key |
| `ACTOR_API_KEY` | 否 | ← OPENAI_API_KEY | Actor 模型 Key |
| `ACTOR_BASE_URL` | 否 | `https://api.openai.com/v1` | Actor 端点 |
| `ACTOR_MODEL` | 否 | `gpt-4o` | Actor 模型名 |
| `CRITIC_API_KEY` | 否 | ← ACTOR_API_KEY | Critic 模型 Key |
| `CRITIC_BASE_URL` | 否 | ← ACTOR_BASE_URL | Critic 端点 |
| `CRITIC_MODEL` | 否 | `gpt-4o-mini` | Critic 模型名 |

*至少需要设置 `OPENAI_API_KEY` 或 `ACTOR_API_KEY`

### 4.2 API 安全配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TOKENRUN_API_KEY` | 空 (禁用) | API 认证 Key |
| `TOKENRUN_CORS_ORIGINS` | `localhost:3000` | CORS 允许的源 (逗号分隔) |

### 4.3 提供商配置示例

**OpenAI:**
```bash
OPENAI_API_KEY=sk-...
ACTOR_MODEL=gpt-4o
CRITIC_MODEL=gpt-4o-mini
```

**DeepSeek:**
```bash
ACTOR_BASE_URL=https://api.deepseek.com/v1
ACTOR_API_KEY=sk-deepseek-...
ACTOR_MODEL=deepseek-chat
CRITIC_BASE_URL=https://api.deepseek.com/v1
CRITIC_API_KEY=sk-deepseek-...
CRITIC_MODEL=deepseek-chat
```

**Ollama (本地):**
```bash
ACTOR_BASE_URL=http://localhost:11434/v1
ACTOR_API_KEY=ollama
ACTOR_MODEL=llama3
CRITIC_BASE_URL=http://localhost:11434/v1
CRITIC_API_KEY=ollama
CRITIC_MODEL=llama3
```

---

## 5. 生产配置建议

### 5.1 安全

```bash
# 1. 启用 API 认证
TOKENRUN_API_KEY=your-strong-secret-key

# 2. 限制 CORS
TOKENRUN_CORS_ORIGINS=https://your-domain.com

# 3. 使用 HTTPS (反向代理)
# nginx/caddy 配置 SSL
```

### 5.2 性能

```yaml
# docker-compose.yml
services:
  backend:
    environment:
      - WEB_CONCURRENCY=4  # uvicorn workers
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
```

### 5.3 监控

```bash
# 健康检查
curl http://localhost:8000/health

# 查看日志
docker-compose logs -f backend --tail 100

# SQLite 数据库大小
ls -lh logs/tokenrun_traces.db
```

### 5.4 备份

```bash
# 备份技能库
tar -czf vault_backup.tar.gz vault/

# 备份 traces
cp logs/tokenrun_traces.db logs/tokenrun_traces.db.bak

# 备份配置
cp .env .env.bak
```

---

## 6. Kubernetes 部署 (参考)

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tokenrun-backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: tokenrun-backend
  template:
    metadata:
      labels:
        app: tokenrun-backend
    spec:
      containers:
        - name: backend
          image: tokenrun-backend:latest
          ports:
            - containerPort: 8000
          env:
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: tokenrun-secrets
                  key: openai-api-key
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 30
          volumeMounts:
            - name: logs
              mountPath: /app/logs
            - name: vault
              mountPath: /app/vault
      volumes:
        - name: logs
          persistentVolumeClaim:
            claimName: tokenrun-logs
        - name: vault
          persistentVolumeClaim:
            claimName: tokenrun-vault
```

---

## 7. 故障排除

| 问题 | 排查步骤 |
|------|----------|
| 容器启动失败 | `docker-compose logs backend` 查看错误 |
| API 无响应 | `curl localhost:8000/health` 检查状态 |
| 前端无法连接 | 检查 CORS 配置和 API URL |
| 数据库锁定 | 检查 SQLite 文件权限 |
| 内存不足 | 增加容器内存限制 |
| API Key 无效 | 检查 .env 配置和提供商状态 |
