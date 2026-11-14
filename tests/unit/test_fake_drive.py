import hashlib

from tests.fakes.drive import FOLDER_MIME_TYPE, FakeDriveClient


def test_folders_are_listed_apart_from_files() -> None:
    drive = FakeDriveClient()
    drive.add_folder("inbox", "d1", "song_micro")
    drive.add_file("inbox", "v1", b"loose video")
    drive.add_file("d1", "v2", b"set video")
    drive.add_file("d1", "t1", b"song: x\nhi\n", name="texts.txt", mime_type="text/plain")

    assert [(f.id, f.mime_type) for f in drive.list_folders("inbox")] == [("d1", FOLDER_MIME_TYPE)]
    assert [f.id for f in drive.list_files("inbox")] == ["v1"]
    assert [f.id for f in drive.list_videos("inbox")] == ["v1"]
    assert [f.id for f in drive.list_files("d1")] == ["v2", "t1"]


def test_files_can_be_removed_and_replaced() -> None:
    drive = FakeDriveClient()
    drive.add_file("d1", "v1", b"first")
    drive.add_file("d1", "v2", b"second")

    drive.replace_content("v1", b"edited")
    drive.remove("v2")

    (listed,) = drive.list_files("d1")
    assert listed.id == "v1"
    assert listed.md5 == hashlib.md5(b"edited").hexdigest()
    assert listed.size_bytes == 6
    assert drive.contents["v1"] == b"edited"
