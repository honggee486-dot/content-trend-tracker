from __future__ import annotations

import src.web_update_ui as update_ui


class _Context:
    def __init__(self, owner, label: str) -> None:
        self.owner = owner
        self.label = label

    def __enter__(self):
        self.owner.entered.append(self.label)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.owner.exited.append(self.label)
        return False


class _Column:
    def __init__(self, owner, index: int) -> None:
        self.owner = owner
        self.index = index

    def popover(self, label, **kwargs):
        self.owner.popovers.append((self.index, label, kwargs))
        return _Context(self.owner, f"popover:{label}")


class _FakeStreamlit:
    def __init__(self) -> None:
        self.container_calls = []
        self.column_calls = []
        self.popovers = []
        self.entered = []
        self.exited = []
        self.errors = []

    def container(self, *args, **kwargs):
        self.container_calls.append((args, kwargs))
        return _Context(self, f"container:{kwargs.get('key') or ''}")

    def columns(self, spec, *args, **kwargs):
        self.column_calls.append((list(spec), args, kwargs))
        return [_Column(self, index) for index in range(len(spec))]

    def error(self, message):
        self.errors.append(str(message))


def test_update_popover_is_rendered_in_last_top_navigation_column(monkeypatch) -> None:
    fake = _FakeStreamlit()
    rendered = []
    monkeypatch.setattr(
        update_ui,
        "render_web_update_panel",
        lambda st_module, *, compact=False: rendered.append((st_module, compact)),
    )

    update_ui.install_web_update_top_navigation_ui(fake)
    with fake.container(key=update_ui.TOP_NAVIGATION_KEY):
        columns = fake.columns([1.05, 0.68, 0.78, 0.95], gap="small")
        assert len(columns) == 4

    assert fake.popovers == [
        (3, update_ui.UPDATE_POPOVER_LABEL, {"use_container_width": True})
    ]
    assert rendered == [(fake, True)]
    assert fake.errors == []


def test_unrelated_container_does_not_render_update_popover(monkeypatch) -> None:
    fake = _FakeStreamlit()
    rendered = []
    monkeypatch.setattr(
        update_ui,
        "render_web_update_panel",
        lambda st_module, *, compact=False: rendered.append((st_module, compact)),
    )

    update_ui.install_web_update_top_navigation_ui(fake)
    with fake.container(key="trend_action_source_row"):
        fake.columns([1, 1, 1])

    assert fake.popovers == []
    assert rendered == []


def test_top_navigation_installer_is_idempotent() -> None:
    fake = _FakeStreamlit()

    update_ui.install_web_update_top_navigation_ui(fake)
    first_container = fake.container
    first_columns = fake.columns
    update_ui.install_web_update_top_navigation_ui(fake)

    assert fake.container is first_container
    assert fake.columns is first_columns
