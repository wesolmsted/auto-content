from pathlib import Path

import pytest

from engine.storage.media_store import LocalMediaStore, MediaKind


def test_path_for_is_absolute_under_the_kind_dir_which_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = LocalMediaStore(Path("data/media"))
    path = store.path_for(MediaKind.SHOTS, "shot_12.mp4")
    assert path == tmp_path / "data" / "media" / "shots" / "shot_12.mp4"
    assert path.parent.is_dir()
    assert not path.exists()


def test_same_name_and_kind_give_the_same_path(tmp_path: Path) -> None:
    store = LocalMediaStore(tmp_path)
    assert store.path_for(MediaKind.RENDERS, "r1.mp4") == store.path_for(
        MediaKind.RENDERS, "r1.mp4"
    )
    assert store.path_for(MediaKind.RENDERS, "r1.mp4") != store.path_for(
        MediaKind.SCRATCH, "r1.mp4"
    )


@pytest.mark.parametrize("name", ["", ".", "..", "../escape.mp4", "a/b.mp4", "/etc/passwd"])
def test_names_must_be_plain_file_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        LocalMediaStore(tmp_path).path_for(MediaKind.SCRATCH, name)


def test_ensure_dirs_creates_every_kind(tmp_path: Path) -> None:
    LocalMediaStore(tmp_path / "media").ensure_dirs()
    assert sorted(path.name for path in (tmp_path / "media").iterdir()) == [
        "renders",
        "scratch",
        "shots",
    ]
