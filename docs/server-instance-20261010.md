# 2026-10-10 服务器部署记录

## 已部署实例

- SSH：`user@119.147.202.74:8005`，映射到 Ubuntu 22.04 服务器 `172.16.1.5:22`。
- 应用目录：`/opt/adhesive-ai-lab`。
- 服务：`adhesive-ai.service`，使用独立系统账号 `adhesive-ai`，已启用开机启动和失败重启。
- 应用监听：`127.0.0.1:8501`；Nginx 代理监听：`127.0.0.1:8080`。
- 数据库：服务器本机 MySQL 8.0，独立数据库 `adhesive_ai_lab`，应用账号仅授权该数据库。连接配置保存在服务器 `.env`，文件权限为 `600`。
- 数据目录：`work/` 和 `outputs/` 由服务账号读写，权限为 `700`。
- Python 3.10.12；完整依赖版本已记录在 `deploy/requirements-server.lock.txt`。

首次部署按用户选择配置网页应用和独立数据库，当时未迁移历史记录和模型。同日随后已安装 VASP/LAMMPS 并配置计算资源，详见 [计算引擎配置记录](server-engines-20261010.md)。2026-10-12 已完成历史数据库和现有模型文件迁移，本机改用服务器共享主库，详见 [数据迁移记录](server-database-sync-20261012.md)。

## 从本机访问

在项目目录打开 PowerShell，执行：

```powershell
.\run_server_tunnel.ps1
```

当前脚本使用已配置的专用转发密钥，并同时转发数据库端口；首次确认服务器身份后打开：

```text
http://127.0.0.1:18501
```

保持隧道终端运行，关闭终端或按 Ctrl+C 会断开隧道。服务器上的应用服务仍会继续运行。若本机 18501 端口已占用，可指定其他端口：

```powershell
.\run_server_tunnel.ps1 -LocalPort 18502
```

对应访问 `http://127.0.0.1:18502`。

## 已完成验证

- MySQL 表初始化及真实读写探针通过。
- Python `pip check` 通过。
- 应用和反向代理健康接口均返回 `ok`。
- Streamlit AppTest 执行主页面和“外部计算可视化”页面，均无运行异常、无错误元素。
- 服务状态为 `active/running`，应用和 MySQL 均已启用开机启动。
- 服务器原有 Nginx 80/8888 端口路由保持原配置。

部署中修复了系统 unzip 对中文文件名的编码转换。后续从 ZIP 更新时，使用 Python `zipfile` 解压 UTF-8 文件名，并保留服务器 `.env`、`work/`、`outputs/` 和已配置的计算资源。

## 运维命令

先通过 SSH 登录服务器：

```powershell
ssh -p 8005 user@119.147.202.74
```

然后执行：

```bash
sudo systemctl status adhesive-ai --no-pager
sudo journalctl -u adhesive-ai -n 100 --no-pager
curl --fail http://127.0.0.1:8501/_stcore/health
```

更新或重启前确认没有正在执行的计算任务，并备份数据库及数据目录。重启应用会终止该服务中的后台计算进程：

```bash
sudo systemctl restart adhesive-ai
```
