"""部署脚本用：为业务平台准备 Nexent 账号（在 nexent-config 容器内运行，使用 Nexent 自身的数据库与服务函数）。

由 scripts/deploy.sh 调用，一般不需要手动运行：
  docker cp docker/nexent/account_setup.py nexent-config:/tmp/
  docker exec -w /opt/backend -e PYTHONPATH=/opt/backend nexent-config \
      python /tmp/account_setup.py --email <账号邮箱> [--emit-key] [--memory off|keep]

--emit-key   在标准输出打印该账号一个有效的 API Key：复用已有的；没有才新建，不会作废该账号的其他 Key
             （Nexent 界面上的「生成 API 密钥」会作废旧 Key）。输出只用于管道直接交给业务平台，不要在终端显示。
--memory off 关闭该账号的智能体记忆与记忆整理（MEMORY_SWITCH / DREAMING_SWITCH = N）。场景智能体以该账号身份运行，
             记忆开着会把其他案件的内容带进当前回答。
其余提示一律写到标准错误。不需要账号密码。
"""
import argparse
import sys

from sqlalchemy import func, select

from database.client import get_db_session
from database.db_models import UserTenant, UserTokenInfo
from database.token_db import create_token, generate_access_key


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--emit-key", action="store_true")
    ap.add_argument("--memory", choices=["off", "keep"], default="keep")
    a = ap.parse_args()

    key = None
    with get_db_session() as db:
        user = db.scalars(select(UserTenant).where(func.lower(UserTenant.user_email) == a.email.strip().lower(),
                                                   UserTenant.delete_flag == "N")
                          .order_by(UserTenant.user_tenant_id)).first()
        if user is None:
            raise SystemExit(f"Nexent 中没有账号 {a.email}：先在 Nexent 注册并登录一次，并配置好模型")
        user_id = user.user_id
        if a.emit_key:
            tok = db.scalars(select(UserTokenInfo).where(UserTokenInfo.user_id == user_id,
                                                         UserTokenInfo.delete_flag == "N")
                             .order_by(UserTokenInfo.token_id.desc())).first()
            key = tok.access_key if tok else None

    if a.emit_key:
        if key:
            log("API Key：复用该账号已有的 Key")
        else:
            key = create_token(generate_access_key(), user_id)["access_key"]
            log("API Key：已为该账号新建 Key")

    if a.memory == "off":
        from management.services.configuration.user.memory import set_dreaming_switch, set_memory_switch
        set_memory_switch(user_id, False)
        set_dreaming_switch(user_id, False)
        log("智能体记忆：已关闭")

    if key:
        sys.stdout.write(key + "\n")


if __name__ == "__main__":
    main()
