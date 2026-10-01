"""Register the topic-aware Feishu factory without importing the official adapter.

Importing Feishu during plugin startup exceeds the loader timeout, and a late
registration is ignored. The official adapter is imported only when the gateway
actually builds the platform.
"""

from __future__ import annotations


def _official(name):
    from plugins.platforms.feishu import adapter as official
    return getattr(official, name)


def register(ctx) -> None:
    def adapter_factory(config):
        from plugins.platforms.feishu.adapter import FeishuAdapter
        from .adapter import LarkTopicAdapter, missing_seams

        absent = missing_seams(FeishuAdapter)
        if absent:
            raise RuntimeError("lark-topics cannot load; Feishu adapter is missing: " + ", ".join(absent))
        return LarkTopicAdapter(config)

    adapter_factory._lark_topics = True
    ctx.register_platform(
        name="feishu",
        label="Feishu / Lark",
        adapter_factory=adapter_factory,
        check_fn=lambda: _official("feishu_deps_present")(),
        ensure_deps_fn=lambda: _official("check_feishu_requirements")(),
        is_connected=lambda config: _official("_is_connected")(config),
        validate_config=lambda config: _official("_is_connected")(config),
        required_env=["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
        install_hint="Run `hermes setup` to install Feishu support.",
        setup_fn=lambda: _official("interactive_setup")(),
        apply_yaml_config_fn=lambda yaml_cfg, platform_cfg: _official("_apply_yaml_config")(yaml_cfg, platform_cfg),
        allowed_users_env="FEISHU_ALLOWED_USERS",
        allow_all_env="FEISHU_ALLOW_ALL_USERS",
        cron_deliver_env_var="FEISHU_HOME_CHANNEL",
        standalone_sender_fn=lambda *args, **kwargs: _official("_standalone_send")(*args, **kwargs),
        max_message_length=8000,
        emoji="🪽",
        allow_update_command=True,
    )
