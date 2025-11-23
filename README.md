# Oblue 蓝圈 
<img src="https://mikusignal.top/img/Logo.png" width="100"><hr>
## ICVE/SPOC 刷课脚本（支持自定义学习时长）

一个可独立运行的命令行脚本，用于在智慧职教（ICVE）SPOC 平台上批量提交学习记录，并支持上传自定义学习时长。

### 已知部分课程无法提交  已做到视频心跳提交但是好像服务器不认（
## 该项目由AI编写

## 主要功能

- 批量遍历并提交当前学期课程的可学习内容
- 支持交互式选择课程并一键提交默认时长
- 支持再次上传自定义学习时长（分钟）
- 自动处理平台加密参数与请求头
- 可选的视频心跳与进度提交逻辑（已在模块内实现）

## 环境要求

- Python 3.8 及以上
- 依赖：`requests`、`pycryptodome`、`tqdm`

安装依赖：

```bash
pip install -r requirements.txt
# 或
pip install requests pycryptodome tqdm
```

## 获取 Token

- 在浏览器登录并打开任意课程页面：`https://zjy2.icve.com.cn/`
- 打开开发者工具（F12）→ Network → 任意接口 → Headers
- 复制 `Authorization` 字段（通常形如 `Bearer xxx...`）
- 首次运行脚本时粘贴该 Token；脚本会保存到本地 `token` 文件，后续直接复用

注：部分情况下也可在 Cookie 中找到 `Token`/`AiToken`/`token`。

## 快速开始

- 独立运行脚本：`zhzj.py`
- 入口在 `zhzj.py:891-894`

运行：

```bash
python zhzj.py
```

交互流程：

1. 首次运行输入 Token（会保存到本地 `token`）
2. 脚本列出当前学期课程，输入序号选择课程（`zhzj.py:80-95`）
3. 自动收集课程内容并提交默认时长（5 分钟）（`zhzj.py:861-870`）
4. 询问是否上传自定义时长，输入正整数分钟将再次批量上传（`zhzj.py:871-886`）

## 脚本要点（代码参考）

- 课程选择：`zhzj.py:58-95`
- 内容收集：`zhzj.py:102-136` 与 `zhzj.py:405-452`
- 加密参数构造：`zhzj.py:139-197`
- 学习记录提交（单条/批量）：`zhzj.py:238-283`、`zhzj.py:284-350`
- 视频心跳与提交：`zhzj.py:680-762`、`zhzj.py:764-846`

## 常见问题

- Token 失效：删除同目录下 `token` 文件并重新运行，按提示重新粘贴 Token
- 提交失败/403/401：重新登录、检查网络或更换浏览器获取新的 `Authorization`
- 依赖安装失败（Windows）：使用管理员权限终端或升级 `pip`（`python -m pip install --upgrade pip`）

## 注意事项

- 本项目仅用于学习与技术研究，请勿用于违反平台服务条款的用途
- 使用脚本产生的风险由使用者自行承担，请在合规范围内使用

## 目录结构（用于开源发布）

- `zhzj.py` 独立运行脚本（含自定义时长上传）
- `requirements.txt` 运行依赖列表
- `README.md` 项目说明

## 致谢

- 平台接口与数据结构来源于智慧职教（ICVE）SPOC
- 开源社区提供的第三方库：`requests`、`pycryptodome`、`tqdm`

  # 我搭建了成品网站
  ## https://mikusignal.top
