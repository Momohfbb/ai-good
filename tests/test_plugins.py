from conftest import run


def test_plugin_off_by_default_then_enabled(tmp_path, make_services):
    svc = make_services()
    p = next(x for x in svc.plugins if x["name"] == "example_unit_converter")
    assert not p["enabled"] and not p["loaded"] and "تحويل" in p["description"]
    assert "convert_units" not in svc.agents["manager"].actions

    from ultra.plugins import file_hash
    import pathlib
    path = pathlib.Path(p["file"])
    svc.db.set_setting("plugins_enabled", {"example_unit_converter": file_hash(path)})
    svc.reload_plugins()
    assert "convert_units" in svc.agents["manager"].actions
    act = svc.agents["manager"].actions["convert_units"]
    res = run(act.fn(None, value="10", from_unit="km", to_unit="mi"))
    assert res.text.startswith("10 km = 6.214 mi")


def test_changed_plugin_is_disabled(tmp_path, make_services):
    svc = make_services()
    user_plugin = svc.data_dir / "plugins" / "hello.py"
    user_plugin.write_text('PLUGIN = {"name": "hello", "description": "يقول مرحبا", "api": 1}\n'
                           'def register(api):\n'
                           '    async def hi(ctx):\n        return api.StepResult("مرحبا")\n'
                           '    api.add_agent("greeter", "المرحب", "Greeter")\n'
                           '    api.add_action("greeter", "hi", "Say hi", {}, hi)\n', encoding="utf-8")
    from ultra.plugins import file_hash
    svc.db.set_setting("plugins_enabled", {"hello": file_hash(user_plugin)})
    svc.reload_plugins()
    assert "greeter" in svc.agents
    user_plugin.write_text(user_plugin.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    svc.reload_plugins()
    assert "greeter" not in svc.agents
    assert next(x for x in svc.plugins if x["name"] == "hello")["error"] == "file_changed_reenable"


def test_broken_plugin_does_not_break_system(make_services):
    svc = make_services()
    bad = svc.data_dir / "plugins" / "broken.py"
    bad.write_text('PLUGIN = {"name": "broken"}\ndef register(api):\n    raise RuntimeError("oops")\n')
    from ultra.plugins import file_hash
    svc.db.set_setting("plugins_enabled", {"broken": file_hash(bad)})
    svc.reload_plugins()
    entry = next(x for x in svc.plugins if x["name"] == "broken")
    assert "oops" in entry["error"] and len(svc.agents) == 14
