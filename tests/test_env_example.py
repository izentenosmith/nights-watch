import re
from pathlib import Path

from nights_watch.bootstrap import Settings

ENV_EXAMPLE = Path(__file__).parent.parent / ".env.example"
ASSIGNMENT = re.compile(r"^(NW_[A-Z0-9_]+)=(.*)$", re.MULTILINE)


def documented() -> dict[str, str]:
    return dict(ASSIGNMENT.findall(ENV_EXAMPLE.read_text()))


def test_every_setting_the_app_reads_is_in_env_example():
    expected = {f"NW_{name.upper()}" for name in Settings.model_fields}
    assert expected <= documented().keys()


def test_secrets_and_destinations_have_no_value_in_env_example():
    for name, value in documented().items():
        if name.endswith(("_URL", "_TOKEN", "_TO", "_PHONE_ID", "_API_KEY", "_KUBECONFIG")):
            assert value == "", name
