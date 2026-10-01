# 团队 Lark 机器人

这是给团队共用的一个 Lark 机器人。同事在群里 @ 它，就可以让它处理文档、任务和 Meegle。它运行在你们自己的一台 Linux 服务器上，不需要向公网开放端口。

## 它会做什么

- 有人在群里 @ 机器人之后，回答留在同一条讨论里。这个人接着说，机器人会继续回应。他改去 @ 别人、而且没有 @ 机器人时，机器人先不回复，只把内容记下。再有人 @ 机器人，才继续。
- 停止、确认只听发起这件事的人。同一条讨论里后来的请求会排队，不打断正在做的事。
- 同事第一次跟机器人说话就能使用，不用验证码。这个开关可以关掉。
- 技能、人设、长期记忆、模型和定时任务，只有管理员能改。谁都可以查看已有的定时任务。程序和密钥，谁都不能在对话里改。
- 机器人发消息、新建和修改任务时，用的是机器人自己的身份。看自己的 Lark 任务，以及查看和处理自己的 Meegle，用的是说话这个人自己的登录。还没登录就告诉他做不到，不会拿别人的登录来用。
- 同一件事的进度和最终回答用同一条消息。做完以后，这条消息变成最终回答。

## 安装前准备

- 一台能上外网的 Linux 服务器，已经安装 Docker 和 Git。
- 能读取这个仓库。
- Lark 开放平台上这个机器人的应用编号和密钥。
- 模型密钥。
- 管理员在 Lark 消息里的标识。可以先空着，装好后补。

下面用 `/var/lib/hermes-team-official` 作为数据和配置的存放位置。如果要换地方，后面所有命令里的这个路径一起换，并写进 `deploy/.env`。

## 安装

```bash
git clone https://github.com/LiuYangArt/hermes-team-deploy.git
cd hermes-team-deploy
cp deploy/.env.example deploy/.env
```

打开 `deploy/.env`，改三项：

- `HERMES_TEAM_STATE_DIR`：数据和配置放在哪。不要放在这个代码仓库里面。
- `HERMES_UID`、`HERMES_GID`：运行这个机器人的系统用户编号，用 `id -u` 和 `id -g` 查看。

不要改里面的程序地址和版本号。构建时会按这两行下载指定版本的程序，再把上面这些团队行为加进去。

```bash
./scripts/build.sh
```

构建需要一些时间，并且要在最终运行的那台服务器上做。不要把别的电脑上做好的镜像拷过来。

构建完成后，建立存放位置并放入空白配置：

```bash
sudo mkdir -p /var/lib/hermes-team-official/data/lark-access /var/lib/hermes-team-official/workspace
sudo chown -R "$(id -u):$(id -g)" /var/lib/hermes-team-official
cp deploy/bot.env.example /var/lib/hermes-team-official/bot.env
cp .build/hermes-core/cli-config.yaml.example /var/lib/hermes-team-official/data/config.yaml
cp extensions/lark-access/config.example.json /var/lib/hermes-team-official/data/lark-access/config.json
cp rules/SOUL.md /var/lib/hermes-team-official/data/SOUL.md
chmod 600 /var/lib/hermes-team-official/bot.env
```

填写这三份文件，示例文字不要留着：

1. `bot.env`：填应用编号和密钥。国际版 Lark 保持 `FEISHU_DOMAIN=lark`。
2. `data/config.yaml`：填模型密钥。这是程序自带的空白模板。
3. `data/lark-access/config.json`：`auto_enroll` 为 `true` 时，同事第一次说话就能使用。`admins` 里写管理员的标识，可以写多个人。

检查通过后再启动：

```bash
python3 deploy/preflight.py \
  --state /var/lib/hermes-team-official \
  --repo . \
  --compose deploy/compose.yaml \
  --dockerfile deploy/Dockerfile
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-deps hermes-team
python3 deploy/healthcheck.py --state /var/lib/hermes-team-official
```

看到「安装前检查通过」和「已经连上」，这台服务器上的机器人就可以用了。检查输出里不会显示密钥。

## 使用时注意

- 不要把别的机器上的聊天记录、个人登录或定时任务拷进这个目录。
- 同一套 Lark 应用不要同时运行两个机器人，否则两边都会收消息。
- 填好的密钥不要提交回这个仓库。
