# Hermes Team

把这个仓库地址发给团队 IT 即可。他克隆这一个私有仓库，按下面的步骤，就能在自己的 Linux 服务器上装好当前这套机器人。不需要本机文件，也不需要再克隆第二个仓库。

话题、进门、个人授权都在这个仓库里，构建时会打进镜像。程序本体用仓库里钉住的公开版本，构建脚本会自己下载，不要另外去拉一份 Hermes。

仓库是私有的。先给他这个仓库的读取权限，再把地址发给他：

https://github.com/LiuYangArt/hermes-team-deploy

应用编号、应用密钥和模型密钥不要放进仓库，另行告诉他。

## 安装

服务器需要能上外网，并已安装 Docker 和 Git。不需要开放入站端口。常见的亚马逊 x86 服务器要在那台机器上构建，不要从别的电脑拷镜像。

```bash
git clone https://github.com/LiuYangArt/hermes-team-deploy.git
cd hermes-team-deploy
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`：把状态目录改成这台机器上、仓库以外的路径；把用户编号改成运行用户的 `id -u` 和 `id -g`。不要改里面的程序地址和版本号。

然后构建。这一步会下载钉住的程序并做出镜像，需要一些时间。

```bash
./scripts/build.sh
```

构建完成后，准备只属于这台机器的配置。下面用 `/var/lib/hermes-team-official` 举例，要和 `deploy/.env` 里写的一致。

```bash
sudo mkdir -p /var/lib/hermes-team-official/data/lark-access /var/lib/hermes-team-official/workspace
sudo chown -R "$(id -u):$(id -g)" /var/lib/hermes-team-official
cp deploy/bot.env.example /var/lib/hermes-team-official/bot.env
cp .build/hermes-core/cli-config.yaml.example /var/lib/hermes-team-official/data/config.yaml
cp extensions/lark-access/config.example.json /var/lib/hermes-team-official/data/lark-access/config.json
cp rules/SOUL.md /var/lib/hermes-team-official/data/SOUL.md
chmod 600 /var/lib/hermes-team-official/bot.env
```

接着填写三处，示例文字不能留着：

1. `bot.env`：开放平台上的应用编号和密钥。国际版 Lark 保持 `FEISHU_DOMAIN=lark`。
2. `data/config.yaml`：这台机器人要用的模型。这是程序自带的空白模板，不要另写一套。
3. `data/lark-access/config.json`：`auto_enroll` 为 `true` 时，同事第一次说话就能进来。`admins` 换成管理员在消息里的标识，可以写多个人。

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

看到「安装前检查通过」和「已经连上」才算这台机器可用。检查输出会藏掉密钥。

不要把旧机器上的聊天、个人登录、定时任务或已经填好的密钥拷进来。同一套应用如果已经有机器人在跑，先停掉旧的再启动。

备份、升级、回滚，以及在这台服务器上的真实对话验收，不在这次安装里。

## 仓库里有什么

- `extensions/`：话题、进门、个人授权。
- `deploy/`：镜像、编排、空白凭据和检查。
- `rules/`：机器人规则模板。
- `jobs/`：尚未随这次安装发布的定时任务。
- `docs/`：内部计划和验收记录。
