# 服务器部署

以下是 Linux + systemd 的部署模板。目标服务器的系统、目录、访问方式和计算环境确认后，再安装对应配置。默认目录为 `/opt/adhesive-ai-lab`，运行用户为 `adhesive-ai`。

## 应用与依赖

上传 `app.py`、`platform_page.py`、`pages/`、`src/`、`scripts/`、`tools/`、`.streamlit/config.toml`、`requirements.txt`、`pyproject.toml` 和 `deploy/`。不上传 Windows `.venv`、`.git` 或缓存。在服务器上使用 Python 3.10 或更新版本重新创建环境；本地已使用 Python 3.12。

```bash
cd /opt/adhesive-ai-lab
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

系统需要提供 Python venv 支持；RDKit 及 ASE 的系统依赖按目标发行版补充。先以部署账号安装依赖，再让服务用户拥有 `work/` 和 `outputs/` 的读写权限。

## 数据与计算环境

在服务器项目根目录配置 `.env`，权限设为 `600`，允许服务用户读取。应用通过 python-dotenv 加载该文件。不要将本机 `.env` 自动打包上传。

- MySQL：根据 `.env.example` 配置服务器可访问的数据库；该示例的占位密码必须替换。候选库和计算结果需要 MySQL，SQLite 回退只覆盖部分记录。初始化使用 `.venv/bin/python scripts/init_database.py`；它创建表、补充身份字段和索引，并验证读写权限。迁移已有数据库前备份并确认迁移范围。
- 本地记录和模型：`work/adhesive_ai_lab.sqlite3`、`work/models/`、`work/jobs/`、`work/campaign_runs/` 等包含历史数据。迁移前停止相关写入，并保留备份；运行记录和模型元数据中的本机绝对路径需要检查。不要直接覆盖服务器已有数据。
- VASP/LAMMPS：网页部署与计算引擎安装分开核验。Linux 部署需要服务用户可执行的求解器、MPI 环境、结构和力场文件，以及有效的资源配置。`work/multiscale_profiles.json`、`work/vasp_resources.json` 中的 Windows/WSL 路径须改为服务器路径。VASP 授权资源按实际授权迁移。

## 常驻服务与访问

确认服务用户、工作目录、虚拟环境和数据权限后，安装服务配置：

```bash
sudo install -m 644 deploy/adhesive-ai.service /etc/systemd/system/adhesive-ai.service
sudo systemctl daemon-reload
sudo systemctl enable --now adhesive-ai
sudo systemctl status adhesive-ai --no-pager
curl --fail http://127.0.0.1:8501/_stcore/health
```

默认只监听服务器回环地址。首次验证可从本机建立 SSH 隧道：

```bash
ssh -N -L 18501:127.0.0.1:8501 -p SSH_PORT SSH_USER@SERVER
```

然后打开 `http://127.0.0.1:18501`。以页面加载、数据库读写和历史记录展示确认应用可用；健康接口只验证 Streamlit 服务启动。

`deploy/nginx.conf` 提供可选的反向代理模板，监听 `127.0.0.1:8080`，包含 WebSocket 转发和 200 MB 上传限制。根据发行版安装到 Nginx 的站点配置目录，执行 `sudo nginx -t` 后再加载。通过 SSH 隧道转发 8080 即可验证代理。

公网访问需要根据实际域名设置监听地址、HTTPS 和访问认证。应用具有文件上传和计算命令执行功能，应先配置授权用户访问，再开放公网入口。保留 Streamlit 的默认 CORS/XSRF 保护。

参考：[Streamlit 反向代理部署](https://docs.streamlit.io/knowledge-base/deploy/deploy-streamlit-domain-port-80)、[Nginx WebSocket 转发](https://nginx.org/en/docs/http/websocket.html)。

## 更新与运维

更新前备份数据库和 `work/`，确认没有正在执行的计算任务。服务停止或重启会终止同一服务下的后台计算进程。

```bash
sudo journalctl -u adhesive-ai -n 100 --no-pager
sudo systemctl restart adhesive-ai
curl --fail http://127.0.0.1:8501/_stcore/health
```

升级时保留服务器 `.env`、数据目录及资源配置，并保存旧代码和依赖版本用于回退。
