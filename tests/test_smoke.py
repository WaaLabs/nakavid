from django.conf import settings


def test_storage_root_setting_is_configurable():
    assert settings.NAKAVID_STORAGE_ROOT is not None
