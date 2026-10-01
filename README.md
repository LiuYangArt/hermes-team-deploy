# 团队 Lark 机器人

## 基于官方hermes做了lark方面的功能增强。更适用于给团队共用。 
- lark 中@机器人自动开启话题并只以话题内容作为对话context
- lark skill 和 meegle skill 修改，每个用户用自己的权限授权。避免单人授权后其它用户通过机器人调用他人授权导致的危险操作。
- 同事第一次跟机器人说话就能使用，不用验证码。这个开关可以关掉。
- 技能、人设、长期记忆、模型和定时任务，只有管理员能改。谁都可以查看已有的定时任务。程序和密钥，谁都不能在对话里改。
- 更安全的权限管理，禁止lark中的对话修改hermes本体。 
- 功能改动都以外挂的形式做。方便后续hermes本体跟随官方版本更新。降低维护成本。 

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

