import re
import os
from unittest.mock import patch, Mock

import pytest

import flytekit
from flytekit.image_spec import ImageSpec
from flytekit.image_spec.default_builder import DefaultImageBuilder, create_docker_context
from flytekit.constants import CopyFileDetection
from pathlib import Path
import tempfile

def test_create_docker_context(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    source_root = tmp_path / "other_files"
    source_root.mkdir()
    hello_world_path = source_root / "hello_world.txt"
    hello_world_path.write_text("hello")

    other_requirements_path = tmp_path / "requirements.txt"
    other_requirements_path.write_text("threadpoolctl\n")

    with tempfile.TemporaryDirectory(dir=Path.cwd().as_posix()) as tmp_dir:
        tmp_file = Path(tmp_dir) / "copy_file.txt"
        tmp_file.write_text("copy_file_content")

        image_spec = ImageSpec(
            name="FLYTEKIT",
            python_version="3.12",
            env={"MY_ENV": "MY_VALUE"},
            apt_packages=["curl"],
            conda_packages=["scipy==1.13.0", "numpy"],
            packages=["pandas==2.2.1"],
            requirements=os.fspath(other_requirements_path),
            source_root=os.fspath(source_root),
            commands=["mkdir my_dir"],
            entrypoint=["/bin/bash"],
            pip_index="https://url.com",
            pip_extra_index_url=["https://extra-url.com"],
            pip_secret_mounts=[(".gitconfig", '/etc/gitconfig'), ("secret_src_2", "secret_dst_2")],
            source_copy_mode=CopyFileDetection.ALL,
            copy=[tmp_file.relative_to(Path.cwd()).as_posix()],
            builder_config={"uv_image": "uv_image_local"},
        )

        create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()

    assert "curl" in dockerfile_content
    assert "scipy==1.13.0 numpy" in dockerfile_content
    assert "python=3.12" in dockerfile_content
    assert "--requirement requirements_uv.txt" in dockerfile_content
    assert "--index-url" in dockerfile_content
    assert "--extra-index-url" in dockerfile_content
    assert "--mount=type=secret,id=secret_0,target=/etc/gitconfig" in dockerfile_content
    assert "--mount=type=secret,id=secret_1,target=secret_dst_2" in dockerfile_content
    assert "COPY --chown=flytekit ./src /root" in dockerfile_content
    assert "uv_image_local" in dockerfile_content
    assert "mambaorg/micromamba:2.0.3-debian12-slim" in dockerfile_content

    run_match = re.search(r"RUN.+mkdir my_dir", dockerfile_content)
    assert run_match
    assert "ENTRYPOINT [\"/bin/bash\"]" in dockerfile_content
    assert "mkdir -p $HOME" in dockerfile_content
    assert f"COPY --chown=flytekit {tmp_file.relative_to(Path.cwd()).as_posix()} /root/" in dockerfile_content

    requirements_path = docker_context_path / "requirements_uv.txt"
    assert requirements_path.exists()

    requirements_content = requirements_path.read_text()
    assert "pandas==2.2.1" in requirements_content
    assert "threadpoolctl" in requirements_content

    tmp_hello_world = docker_context_path / "src" / "hello_world.txt"
    assert tmp_hello_world.exists()
    assert tmp_hello_world.read_text() == "hello"


def test_create_docker_context_with_git_subfolder(tmp_path):
    # uv's pip install errors with git and subdirectory
    # In this case, we go back to pip instead
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        apt_packages=["git"],
        packages=["git+https://github.com/flyteorg/flytekit.git@master#subdirectory=plugins/flytekit-wandb"],
    )

    create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()

    assert "--requirement requirements_uv.txt" in dockerfile_content
    requirements_path = docker_context_path / "requirements_uv.txt"
    assert requirements_path.exists()


def test_create_docker_context_with_null_entrypoint(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        entrypoint=[],
    )

    create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()
    assert "ENTRYPOINT []" in dockerfile_content


@pytest.mark.parametrize("flytekit_spec", [None, "flytekit>=1.12.3", "flytekit==1.12.3"])
def test_create_docker_context_with_flytekit(tmp_path, flytekit_spec, monkeypatch):

    # pretend version is 1.13.0
    mock_version = "1.13.0"
    monkeypatch.setattr(flytekit, "__version__", mock_version)

    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    if flytekit_spec:
        packages = [flytekit_spec]
    else:
        packages = []

    image_spec = ImageSpec(
        name="FLYTEKIT", packages=packages
    )

    create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()

    requirements_path = docker_context_path / "requirements_uv.txt"
    assert requirements_path.exists()

    requirements_content = requirements_path.read_text()
    if flytekit_spec:
        flytekit_spec in requirements_content
        assert f"flytekit=={mock_version}" not in requirements_content
    else:
        assert f"flytekit=={mock_version}" in requirements_content


def test_create_docker_context_cuda(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    image_spec = ImageSpec(cuda="12.4.1", cudnn="8")

    msg = "cuda and cudnn do not need to be specified. If you are installed"

    with pytest.raises(ValueError, match=msg):
        create_docker_context(image_spec, docker_context_path)


@pytest.mark.skipif(
    os.environ.get("_FLYTEKIT_TEST_DEFAULT_BUILDER", "0") == "0",
    reason="Set _FLYTEKIT_TEST_DEFAULT_BUILDER=1 to run this test",
)
def test_build(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    source_root = tmp_path / "other_files"
    source_root.mkdir()
    hello_world_path = source_root / "hello_world.txt"
    hello_world_path.write_text("hello")

    other_requirements_path = tmp_path / "requirements.txt"
    other_requirements_path.write_text("threadpoolctl\n")

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        env={"MY_ENV": "MY_VALUE"},
        apt_packages=["curl"],
        conda_packages=["scipy==1.13.0", "numpy"],
        packages=["pandas==2.2.1"],
        requirements=os.fspath(other_requirements_path),
        source_root=os.fspath(source_root),
        commands=["mkdir my_dir"],
        copy=[f"{tmp_path}/hello_world.txt", f"{tmp_path}/requirements.txt"]
    )

    builder = DefaultImageBuilder()

    builder.build_image(image_spec)


@pytest.mark.parametrize("push_image_spec", ["0", "1"])
def test_should_push_env(monkeypatch, push_image_spec):
    image_spec = ImageSpec(name="my_flytekit", python_version="3.12", registry="localhost:30000")
    monkeypatch.setenv("FLYTE_PUSH_IMAGE_SPEC", push_image_spec)

    run_mock = Mock()
    monkeypatch.setattr("flytekit.image_spec.default_builder.run", run_mock)

    builder = DefaultImageBuilder()
    builder.build_image(image_spec)

    run_mock.assert_called_once()
    call_args = run_mock.call_args.args

    if push_image_spec == "0":
        assert "--push" not in call_args[0]
    else:
        assert "--push" in call_args[0]


def test_build_raises_if_tag_changed():
    image_spec = ImageSpec(name="my_flytekit", python_version="3.12", registry="localhost:30000")
    image_spec.tag = "arbitrarily_set_cached_tag" # This simulates a tag change due to file changes.
    builder = DefaultImageBuilder()
    with pytest.raises(ValueError, match="image tag changed"):
        builder.build_image(image_spec)


def test_create_docker_context_uv_lock(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    uv_lock_file = tmp_path / "uv.lock"
    uv_lock_file.write_text("this is a lock file")

    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text("this is a pyproject.toml file")

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        requirements=os.fspath(uv_lock_file),
        pip_index="https://url.com",
        pip_extra_index_url=["https://extra-url.com"],
        pip_extra_args="--no-install-package library-to-skip",
    )

    warning_msg = "uv.lock support is experimental"
    with pytest.warns(UserWarning, match=warning_msg):
        create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()

    assert (
        "uv sync --index-url https://url.com --extra-index-url "
        "https://extra-url.com --no-install-package library-to-skip "
        "--locked --no-dev --no-install-project"
    ) in dockerfile_content


@pytest.mark.parametrize("lock_file", ["uv.lock", "poetry.lock"])
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_lock_errors_no_pyproject_toml(monkeypatch, tmp_path, lock_file):
    run_mock = Mock()
    monkeypatch.setattr("flytekit.image_spec.default_builder.run", run_mock)

    lock_file_path = tmp_path / lock_file
    lock_file_path.write_text("this is a lock file")

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        requirements=os.fspath(lock_file_path),
    )

    builder = DefaultImageBuilder()

    with pytest.raises(ValueError, match="a pyproject.toml file must be in the same"):
        builder.build_image(image_spec)


@pytest.mark.parametrize("lock_file", ["uv.lock", "poetry.lock"])
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_uv_lock_error_no_packages(monkeypatch, tmp_path, lock_file):
    run_mock = Mock()
    monkeypatch.setattr("flytekit.image_spec.default_builder.run", run_mock)

    lock_file_path = tmp_path / lock_file
    lock_file_path.write_text("this is a lock file")

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        requirements=os.fspath(lock_file_path),
        packages=["ruff"],
    )
    builder = DefaultImageBuilder()

    with pytest.raises(ValueError, match=f"Support for {lock_file} files and packages is mutually exclusive"):
        builder.build_image(image_spec)

    run_mock.assert_not_called()


def test_create_poetry_lock(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    poetry_lock = tmp_path / "poetry.lock"
    poetry_lock.write_text("this is a lock file")

    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text("this is a pyproject.toml file")

    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        requirements=os.fspath(poetry_lock),
        pip_extra_args="--no-directory",
    )

    create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()

    assert "poetry install --no-directory --no-root" in dockerfile_content


def test_python_exec(tmp_path):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()
    base_image = "ghcr.io/flyteorg/flytekit:py3.11-1.14.4"
    python_exec = "/usr/local/bin/python"

    image_spec = ImageSpec(
        name="FLYTEKIT",
        base_image=base_image,
        python_exec=python_exec
    )

    create_docker_context(image_spec, docker_context_path)

    dockerfile_path = docker_context_path / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text()

    assert f"UV_PYTHON={python_exec}" in dockerfile_content


@pytest.mark.parametrize("key, value", [("conda_packages", ["ruff"]), ("conda_channels", ["bioconda"])])
def test_python_exec_errors(tmp_path, key, value):
    docker_context_path = tmp_path / "builder_root"
    docker_context_path.mkdir()

    image_spec = ImageSpec(
        name="FLYTEKIT",
        base_image="ghcr.io/flyteorg/flytekit:py3.11-1.14.4",
        python_exec="/usr/local/bin/python",
        **{key: value}
    )
    msg = f"{key} is not supported with python_exec"
    with pytest.raises(ValueError, match=msg):
        create_docker_context(image_spec, docker_context_path)


def test_github_credential_as_secret(monkeypatch, recwarn):
    image_spec = ImageSpec(
        name="FLYTEKIT",
        python_version="3.12",
        pip_secret_mounts=[(".gitconfig", '/etc/gitconfig'), ("secret_src_2", "secret_dst_2")],
    )
    run_mock = Mock()
    monkeypatch.setattr("flytekit.image_spec.default_builder.run", run_mock)

    builder = DefaultImageBuilder()
    builder.build_image(image_spec)

    run_mock.assert_called_once()
    call_args = run_mock.call_args.args

    assert "--secret" in call_args[0]
    first_secret = call_args[0].index("--secret")
    assert call_args[0][first_secret + 1] == "id=secret_0,src=.gitconfig"
    assert call_args[0][first_secret + 2] == "--secret"
    assert call_args[0][first_secret + 3] == "id=secret_1,src=secret_src_2"

    assert not len(recwarn)
