"""Entry point: python -m ai_bridge"""
import sys


def _run():
    from ai_bridge.app import main
    main()


if __name__ == "__main__":
    try:
        _run()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print("=" * 60)
        print("应用启动失败：", e)
        print("=" * 60)
        traceback.print_exc()
        print("=" * 60)
        input("按回车退出...")
