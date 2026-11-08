from agentbox_cli.detect import Detected, repo_name, scan


def _write(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_dotnet_android_repo(tmp_path):
    lib = tmp_path / "lib"
    app = tmp_path / "app"
    _write(lib, {"Lib.sln": "", "global.json": '{"sdk": {"version": "10.0.100", "rollForward": "latestFeature"}}'})
    _write(app, {
        "App.sln": "",
        "eng/sdk.json": '{"sdk": {"version": "10.0.401"}}',
        "src/App.Android/App.Android.csproj":
            "<Project><PropertyGroup><TargetFramework>net10.0-android</TargetFramework></PropertyGroup>"
            '<ItemGroup><PackageReference Include="Avalonia.Android" Version="11" />'
            '<ProjectReference Include="..\\..\\..\\lib\\src\\Lib\\Lib.csproj" /></ItemGroup></Project>',
        ".github/workflows/android.yml":
            "env:\n  NDK_VERSION: 28.2.13676358\n  CMAKE_VERSION: 3.31.6\njobs:\n  b:\n    steps:\n"
            "      - uses: actions/setup-java@v4\n        with:\n          java-version: '21'\n"
            "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.12'\n"
            "      - run: pip install pyyaml==6.0.2\n"
            '      - run: sdkmanager --install "ndk;$NDK_VERSION" "cmake;$CMAKE_VERSION"\n',
    })
    d = scan(app, "app")
    assert d.dotnet_version == "10.0.401"
    assert d.workloads == {"android"}
    assert {"platforms;android-36", "build-tools;36.0.0", "ndk;28.2.13676358", "cmake;3.31.6",
            "platform-tools"} <= d.sdk_packages
    assert d.jdk == 21 and d.python == "3.12" and d.python_pkgs == {"pyyaml==6.0.2"}
    assert d.gui and "libfontconfig1" in d.apt
    assert d.siblings == {"lib"}
    assert d.setup == ["dotnet restore App.sln"]


def test_merge_takes_highest_versions(tmp_path):
    a, b = Detected(name="a", jdk=17, python="3.11"), Detected(name="b", jdk=21, python="3.12")
    a.notes.append("x")
    total = Detected(name="all")
    total.merge(a)
    total.merge(b)
    assert total.jdk == 21 and total.python == "3.12" and total.notes == ["a: x"]


def test_node_python_rust(tmp_path):
    _write(tmp_path, {"package.json": "{}", "pnpm-lock.yaml": "", "pyproject.toml": 'requires-python = ">=3.11"',
                      "uv.lock": "", "Cargo.toml": ""})
    d = scan(tmp_path, "x")
    assert d.node and {"uv", "python", "rust"} <= d.tools and d.python == "3.11"
    assert any("pnpm install" in s for s in d.setup) and "uv sync" in d.setup and "cargo fetch" in d.setup


def test_gradle_android(tmp_path):
    _write(tmp_path, {"app/build.gradle.kts": 'plugins { id("com.android.application") }\nandroid { compileSdk = 35 }',
                      "gradlew": ""})
    d = scan(tmp_path, "g")
    assert d.android and "platforms;android-35" in d.sdk_packages and d.jdk == 17


def test_repo_name():
    assert repo_name("git@github.com:org/travelrepo.git") == "travelrepo"
    assert repo_name("https://github.com/org/jourfold") == "jourfold"
    assert repo_name("git@host:solo.git") == "solo"
