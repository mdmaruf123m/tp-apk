from io import BytesIO

from PIL import Image

from scripts import build_apk


def png_bytes(size=(256, 180), color=(30, 100, 220, 255)):
    image = Image.new("RGBA", size, color)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_apply_icon_creates_adaptive_and_density_icons(tmp_path, monkeypatch):
    monkeypatch.setattr(build_apk, "ROOT", tmp_path)
    (tmp_path / "app/src/main/res/drawable").mkdir(parents=True)
    for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
        (tmp_path / f"app/src/main/res/mipmap-{density}").mkdir(parents=True)

    build_apk.apply_icon(png_bytes())

    assert (tmp_path / "app/src/main/res/drawable/ic_launcher_foreground_asset.png").is_file()
    expected = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for density, size in expected.items():
        icon = Image.open(tmp_path / f"app/src/main/res/mipmap-{density}/ic_launcher.png")
        round_icon = Image.open(tmp_path / f"app/src/main/res/mipmap-{density}/ic_launcher_round.png")
        assert icon.size == (size, size)
        assert round_icon.size == (size, size)
        assert round_icon.mode == "RGBA"


def test_apply_splash_points_resource_to_generated_image(tmp_path, monkeypatch):
    monkeypatch.setattr(build_apk, "ROOT", tmp_path)
    drawable = tmp_path / "app/src/main/res/drawable"
    drawable.mkdir(parents=True)

    build_apk.apply_splash(png_bytes())

    assert Image.open(drawable / "ic_splash_custom.png").size == (512, 512)
    assert "@drawable/ic_splash_custom" in (drawable / "ic_splash_logo.xml").read_text()


def test_html_mode_applies_custom_css_and_javascript(tmp_path, monkeypatch):
    monkeypatch.setattr(build_apk, "ROOT", tmp_path)
    target = tmp_path / "app/src/main/assets/web/index.html"
    target.parent.mkdir(parents=True)
    payload = {
        "config": {
            "resource_type": "html",
            "html_code": "<html><head></head><body>Hello</body></html>",
            "advanced": {"custom_css": "body{color:red}", "custom_javascript": "console.log('ok')"},
        },
        "files": {},
    }

    build_apk.write_html(payload)
    result = target.read_text()
    assert "body{color:red}" in result
    assert "console.log('ok')" in result
