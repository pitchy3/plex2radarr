from pathlib import Path
from types import SimpleNamespace

import pytest

from plex2radarr.config import (
    AppConfig,
    PathMapping,
    PlexConfig,
    QBittorrentConfig,
    RadarrConfig,
)
from plex2radarr.models import (
    ExternalIds,
    PlanItem,
    PlexMovie,
    PlexScanIssue,
    PlexScanResult,
    PlexScanStats,
    TorrentMatch,
)
from plex2radarr.paths import PathMappingError
from plex2radarr.radarr import RadarrError
from plex2radarr.reconcile import Reconciler, SelectionError
from plex2radarr.state import StateStore


class FakePlex:
    def __init__(self, movies):
        self._movies = movies

    def movies(self):
        return self._movies


class FakeScanPlex:
    def __init__(self, scan_result):
        self._scan_result = scan_result

    def scan(self, radarr_local_root):
        return self._scan_result


class FakeRadarr:
    def __init__(self, movies):
        self._movies = movies

    def movies(self):
        return self._movies

    def movie(self, movie_id):
        return next(movie for movie in self._movies if movie["id"] == movie_id)


class FakeQbit:
    def __init__(self, name, matches, matches_by_hash=None, relocation_root="/torrents/movies"):
        self.config = SimpleNamespace(name=name, relocation_root=relocation_root)
        self._matches = matches
        self._matches_by_hash = matches_by_hash or {}

    def find_matches(self, path):
        return list(self._matches.get(path, []))

    def matches_for_hash(self, torrent_hash):
        explicit = self._matches_by_hash.get(torrent_hash)
        if explicit is not None:
            return list(explicit)

        matches = []
        seen = set()
        for values in self._matches.values():
            for match in values:
                if match.torrent_hash != torrent_hash:
                    continue
                key = (
                    match.client_name,
                    match.torrent_hash,
                    match.relative_path,
                    match.file_path,
                )
                if key not in seen:
                    seen.add(key)
                    matches.append(match)
        return matches


def config(path_mappings=()):
    return AppConfig(
        plex=PlexConfig("http://plex", "token", "Movies"),
        radarr=RadarrConfig("http://radarr", "key", "/movies", "Any"),
        qbittorrent=(
            QBittorrentConfig("main", "http://qbit", "u", "p", "/torrents/movies"),
        ),
        path_mappings=tuple(path_mappings),
    )


def test_plan_skips_existing_radarr_movie_with_file():
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), Path("/movies/matrix.mkv"))
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([{"id": 1, "tmdbId": 603, "hasFile": True}]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "skip"
    assert plan[0].radarr_movie_id == 1


def test_plan_reuses_existing_radarr_movie_without_file():
    path = Path("/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([{"id": 1, "tmdbId": 603, "hasFile": False}]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "move_import"
    assert plan[0].radarr_movie_id == 1
    assert "reuse existing missing Radarr movie" in plan[0].reason


def test_plan_relocates_when_qbit_owns_source():
    path = Path("/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    match = TorrentMatch(
        "main",
        "abc",
        "matrix",
        path,
        Path("/movies"),
        1.0,
        Path("matrix.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {path: [match]})],
    )
    plan = r.plan()
    assert plan[0].action == "relocate_and_import"
    assert plan[0].torrent == match


def test_plan_moves_unseeded_legacy_file_inside_library_root():
    path = Path("/movies/legacy/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "move_import"


def test_plan_hardlink_imports_unseeded_file_outside_library_root():
    path = Path("/imports/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )
    plan = r.plan()
    assert plan[0].action == "import"


def test_plan_can_target_one_file():
    matrix = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    alien = PlexMovie(
        "Alien",
        1979,
        ExternalIds(tmdb=348),
        Path("/movies/legacy/alien.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([matrix, alien]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    plan = r.plan(selected_paths=[matrix.file_path])

    assert [item.movie.title for item in plan] == ["The Matrix"]


def test_plan_can_target_multiple_files():
    matrix = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    alien = PlexMovie(
        "Alien",
        1979,
        ExternalIds(tmdb=348),
        Path("/movies/legacy/alien.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([matrix, alien]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    plan = r.plan(selected_paths=[alien.file_path, matrix.file_path])

    assert {item.movie.title for item in plan} == {"Alien", "The Matrix"}


def test_plan_rejects_requested_file_not_in_plex_or_journal():
    movie = PlexMovie(
        "The Matrix",
        1999,
        ExternalIds(tmdb=603),
        Path("/movies/legacy/matrix.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    with pytest.raises(SelectionError, match="not found"):
        r.plan(selected_paths=[Path("/movies/legacy/not-in-plex.mkv")])


def test_recovery_can_target_original_path_after_plex_loses_file(tmp_path: Path):
    original = Path("/movies/legacy/matrix.mkv")
    moved = Path("/torrents/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), original)
    original_match = TorrentMatch(
        "main",
        "abc",
        "matrix",
        original,
        Path("/movies/legacy"),
        1.0,
        Path("matrix.mkv"),
    )
    moved_match = TorrentMatch(
        "main",
        "abc",
        "matrix",
        moved,
        Path("/torrents/movies"),
        1.0,
        Path("matrix.mkv"),
    )
    state = StateStore(tmp_path / "state.json")
    transaction = state.begin(
        movie,
        "relocate_and_import",
        original_match,
        None,
        original,
    )
    state.update(
        transaction.key,
        stage="torrent_relocated",
        current_source=str(moved),
        radarr_movie_id=7,
    )

    r = Reconciler(
        config(),
        plex=FakePlex([]),
        radarr=FakeRadarr([{"id": 7, "tmdbId": 603, "hasFile": False}]),
        qbits=[
            FakeQbit(
                "main",
                {},
                matches_by_hash={"abc": [moved_match]},
            )
        ],
        state=state,
    )

    plan = r.plan(selected_paths=[original])

    assert len(plan) == 1
    assert plan[0].action == "relocate_and_import"
    assert plan[0].source_path == moved
    assert plan[0].radarr_movie_id == 7
    assert "resume interrupted reconciliation" in plan[0].reason


def test_preflight_rejects_radarr_mapping_that_cannot_see_relocation_root(tmp_path: Path):
    original = Path("/Volume2/Media/_Movies/Movie/movie.mkv")
    movie = PlexMovie("Movie", 2020, ExternalIds(tmdb=1), original)
    match = TorrentMatch(
        "main",
        "abc",
        "Movie",
        original,
        Path("/Volume2/Media/_Movies"),
        1.0,
        Path("Movie/movie.mkv"),
    )
    cfg = AppConfig(
        plex=PlexConfig("http://plex", "token", "Movies"),
        radarr=RadarrConfig("http://radarr", "key", "/data/_Movies", "Any"),
        qbittorrent=(
            QBittorrentConfig(
                "main",
                "http://qbit",
                "u",
                "p",
                "/downloads/torrents",
            ),
        ),
        path_mappings=(
            PathMapping(
                "radarr",
                "/data/_Movies",
                "/Volume2/Media/_Movies",
            ),
            PathMapping(
                "qbittorrent:main",
                "/downloads",
                "/Volume2/Media",
            ),
        ),
    )
    r = Reconciler(
        cfg,
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[
            FakeQbit(
                "main",
                {original: [match]},
                relocation_root="/downloads/torrents",
            )
        ],
        state=StateStore(tmp_path / "state.json"),
    )
    plan = r.plan()

    with pytest.raises(PathMappingError, match="not covered"):
        r.preflight(plan)


def test_multi_file_torrent_journals_all_affected_plex_movies(tmp_path: Path):
    first_path = tmp_path / "library" / "one.mkv"
    second_path = tmp_path / "library" / "two.mkv"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("one")
    second_path.write_text("two")

    first = PlexMovie("One", 2001, ExternalIds(tmdb=1), first_path)
    second = PlexMovie("Two", 2002, ExternalIds(tmdb=2), second_path)
    first_match = TorrentMatch(
        "main",
        "samehash",
        "bundle",
        first_path,
        first_path.parent,
        1.0,
        Path("one.mkv"),
    )
    second_match = TorrentMatch(
        "main",
        "samehash",
        "bundle",
        second_path,
        second_path.parent,
        1.0,
        Path("two.mkv"),
    )
    state = StateStore(tmp_path / "state.json")
    qbit = FakeQbit(
        "main",
        {
            first_path: [first_match],
            second_path: [second_match],
        },
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([first, second]),
        radarr=FakeRadarr([]),
        qbits=[qbit],
        state=state,
    )
    current = PlanItem(
        first,
        "relocate_and_import",
        "test",
        torrent=first_match,
    )
    state.begin(first, current.action, first_match, None, first_path)

    r._journal_torrent_companions(
        current,
        qbit,
        tmp_path / "torrents",
    )

    keys = {transaction.key for transaction in state.all()}
    assert keys == {"tmdb:1", "tmdb:2"}


def test_recovery_does_not_resubmit_persisted_radarr_command(tmp_path: Path):
    source = tmp_path / "movie.mkv"
    source.write_text("movie")
    movie = PlexMovie("Movie", 2020, ExternalIds(tmdb=1), source)
    state = StateStore(tmp_path / "state.json")
    transaction = state.begin(movie, "import", None, 7, source)
    state.update(
        transaction.key,
        stage="radarr_import_submitted",
        radarr_movie_id=7,
        radarr_command_id=55,
    )

    class SubmittedCommandRadarr:
        def __init__(self):
            self.import_calls = 0
            self.waited_for = []

        def wait_for_command(self, command_id):
            self.waited_for.append(command_id)
            return {"id": command_id, "status": "completed"}

        def movie(self, movie_id):
            return {"id": movie_id, "tmdbId": 1, "hasFile": True}

        def manual_import_candidates(self, folder):
            raise AssertionError("manual import candidates should not be queried")

        def import_file(self, candidate, movie_id, mode="copy"):
            self.import_calls += 1
            raise AssertionError("import should not be submitted again")

    radarr = SubmittedCommandRadarr()
    r = Reconciler(
        config(),
        plex=FakePlex([]),
        radarr=radarr,
        qbits=[],
        state=state,
    )
    item = PlanItem(
        movie,
        "import",
        "resume",
        radarr_movie_id=7,
        source_path=source,
        transaction_key=transaction.key,
    )

    r.execute(item)

    assert radarr.waited_for == [55]
    assert radarr.import_calls == 0
    assert state.all() == []


def test_plan_lists_all_qbittorrent_owners_for_ambiguous_file():
    path = Path("/movies/matrix.mkv")
    movie = PlexMovie("The Matrix", 1999, ExternalIds(tmdb=603), path)
    first = TorrentMatch(
        "main",
        "aaaaaaaaaaaaaaaa",
        "Matrix tracker A",
        path,
        Path("/movies"),
        1.0,
        Path("matrix.mkv"),
    )
    second = TorrentMatch(
        "main",
        "bbbbbbbbbbbbbbbb",
        "Matrix tracker B",
        path,
        Path("/movies"),
        1.0,
        Path("matrix.mkv"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {path: [first, second]})],
    )

    plan = r.plan()

    assert plan[0].action == "skip"
    assert plan[0].reason == "multiple qBittorrent torrents own this file"
    assert plan[0].notes == [
        "qBittorrent owner: main / Matrix tracker A [aaaaaaaaaaaa]",
        "qBittorrent owner: main / Matrix tracker B [bbbbbbbbbbbb]",
    ]


def test_recovery_movie_does_not_also_get_multi_file_skip(tmp_path: Path):
    first = Path("/movies/movie-a.mkv")
    second = Path("/movies/movie-b.mkv")
    movie = PlexMovie("Movie", 2020, ExternalIds(tmdb=1), first)
    issue = PlexScanIssue(
        title="Movie",
        year=2020,
        ids=ExternalIds(tmdb=1),
        file_paths=(first, second),
        reason="multiple Plex files in configured Radarr root",
    )
    scan = PlexScanResult(
        movies=(),
        issues=(issue,),
        all_files=(
            movie,
            PlexMovie("Movie", 2020, ExternalIds(tmdb=1), second),
        ),
        stats=PlexScanStats(
            total_movies=1,
            eligible_movies=0,
            outside_root_movies=0,
            multiple_applicable_files=1,
            no_media_movies=0,
        ),
    )
    state = StateStore(tmp_path / "state.json")
    state.begin(movie, "move_import", None, 7, first)

    r = Reconciler(
        config(),
        plex=FakeScanPlex(scan),
        radarr=FakeRadarr([{"id": 7, "tmdbId": 1, "hasFile": False}]),
        qbits=[FakeQbit("main", {})],
        state=state,
    )

    plan = r.plan()

    assert len(plan) == 1
    assert plan[0].action == "move_import"
    assert "resume interrupted reconciliation" in plan[0].reason


def test_torrent_relocation_rejects_outside_root_plex_companion(tmp_path: Path):
    selected_path = Path("/movies/selected.mkv")
    outside_path = Path("/other-library/companion.mkv")
    selected = PlexMovie("Selected", 2020, ExternalIds(tmdb=1), selected_path)
    outside = PlexMovie("Companion", 2021, ExternalIds(tmdb=2), outside_path)
    selected_match = TorrentMatch(
        "main",
        "samehash",
        "bundle",
        selected_path,
        Path("/movies"),
        1.0,
        Path("selected.mkv"),
    )
    outside_match = TorrentMatch(
        "main",
        "samehash",
        "bundle",
        outside_path,
        Path("/other-library"),
        1.0,
        Path("companion.mkv"),
    )
    scan = PlexScanResult(
        movies=(selected,),
        issues=(),
        all_files=(selected, outside),
        stats=PlexScanStats(
            total_movies=2,
            eligible_movies=1,
            outside_root_movies=1,
            multiple_applicable_files=0,
            no_media_movies=0,
        ),
    )
    qbit = FakeQbit(
        "main",
        {
            selected_path: [selected_match],
            outside_path: [outside_match],
        },
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=FakeScanPlex(scan),
        radarr=FakeRadarr([]),
        qbits=[qbit],
        state=StateStore(tmp_path / "state.json"),
    )
    item = PlanItem(
        selected,
        "relocate_and_import",
        "test",
        torrent=selected_match,
    )

    with pytest.raises(
        SelectionError,
        match="outside the configured Radarr root",
    ):
        r._journal_torrent_companions(
            item,
            qbit,
            tmp_path / "torrents",
        )


def test_plex_scan_snapshot_is_reused():
    movie = PlexMovie(
        "Movie",
        2020,
        ExternalIds(tmdb=1),
        Path("/movies/movie.mkv"),
    )
    scan = PlexScanResult(
        movies=(movie,),
        issues=(),
        all_files=(movie,),
        stats=PlexScanStats(
            total_movies=1,
            eligible_movies=1,
            outside_root_movies=0,
            multiple_applicable_files=0,
            no_media_movies=0,
        ),
    )

    class CountingScanPlex:
        def __init__(self):
            self.calls = 0

        def scan(self, radarr_local_root):
            self.calls += 1
            return scan

    plex = CountingScanPlex()
    r = Reconciler(
        config(),
        plex=plex,
        radarr=FakeRadarr([]),
        qbits=[FakeQbit("main", {})],
    )

    first = r._plex_scan()
    second = r._plex_scan()

    assert first == second
    assert plex.calls == 1


def test_radarr_movie_snapshot_is_reused():
    class CountingRadarr(FakeRadarr):
        def __init__(self, movies):
            super().__init__(movies)
            self.calls = 0

        def movies(self):
            self.calls += 1
            return super().movies()

    radarr = CountingRadarr([{"id": 1, "tmdbId": 1, "hasFile": False}])
    r = Reconciler(
        config(),
        plex=FakePlex([]),
        radarr=radarr,
        qbits=[],
    )

    assert r._radarr_movies() == [{"id": 1, "tmdbId": 1, "hasFile": False}]
    assert r._radarr_movies() == [{"id": 1, "tmdbId": 1, "hasFile": False}]
    assert radarr.calls == 1


def test_radarr_snapshot_updates_after_movie_refresh():
    radarr = FakeRadarr([{"id": 1, "tmdbId": 1, "hasFile": False}])
    r = Reconciler(
        config(),
        plex=FakePlex([]),
        radarr=radarr,
        qbits=[],
    )
    r._radarr_movies()
    r._update_radarr_snapshot({"id": 1, "tmdbId": 1, "hasFile": True})

    assert r._radarr_movies() == [{"id": 1, "tmdbId": 1, "hasFile": True}]


def test_radarr_snapshot_updates_immediately_after_add_before_import_failure(tmp_path: Path):
    source = tmp_path / "movie.mkv"
    source.write_text("movie")
    movie = PlexMovie("Movie", 2020, ExternalIds(tmdb=1), source)

    class AddThenFailRadarr:
        def __init__(self):
            self._movies = []

        def movies(self):
            return list(self._movies)

        def add_movie(self, movie):
            added = {"id": 7, "tmdbId": 1, "imdbId": None, "hasFile": False}
            self._movies.append(dict(added))
            return added

        def manual_import_candidates(self, folder):
            return []

    radarr = AddThenFailRadarr()
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=radarr,
        qbits=[],
        state=StateStore(tmp_path / "state.json"),
    )
    r._radarr_movies()

    item = PlanItem(movie, "move_import", "test")

    with pytest.raises(RadarrError):
        r.execute(item)

    assert r._radarr_movies() == [
        {"id": 7, "tmdbId": 1, "imdbId": None, "hasFile": False}
    ]


def test_companion_safety_refreshes_current_state_for_multifile_torrent(tmp_path: Path):
    first_path = tmp_path / "library" / "one.mkv"
    second_path = tmp_path / "library" / "two.mkv"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("one")
    second_path.write_text("two")

    first = PlexMovie("One", 2001, ExternalIds(tmdb=1), first_path)
    second = PlexMovie("Two", 2002, ExternalIds(tmdb=2), second_path)
    first_match = TorrentMatch(
        "main", "samehash", "bundle", first_path, first_path.parent, 1.0, Path("one.mkv")
    )
    second_match = TorrentMatch(
        "main", "samehash", "bundle", second_path, second_path.parent, 1.0, Path("two.mkv")
    )

    class RefreshingScanPlex:
        def __init__(self):
            self.calls = 0

        def scan(self, radarr_local_root):
            self.calls += 1
            return PlexScanResult(
                movies=(first, second),
                issues=(),
                all_files=(first, second),
                stats=PlexScanStats(
                    total_movies=2,
                    eligible_movies=2,
                    outside_root_movies=0,
                    multiple_applicable_files=0,
                    no_media_movies=0,
                ),
            )

    class RefreshingRadarr(FakeRadarr):
        def __init__(self):
            super().__init__([{"id": 2, "tmdbId": 2, "hasFile": True}])
            self.calls = 0

        def movies(self):
            self.calls += 1
            return super().movies()

    class MultiFileQbit(FakeQbit):
        def matches_for_hash(self, torrent_hash):
            return [first_match, second_match]

    plex = RefreshingScanPlex()
    radarr = RefreshingRadarr()
    qbit = MultiFileQbit(
        "main",
        {first_path: [first_match], second_path: [second_match]},
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=plex,
        radarr=radarr,
        qbits=[qbit],
        state=StateStore(tmp_path / "state.json"),
    )
    r._plex_scan()
    r._radarr_movies()

    item = PlanItem(first, "relocate_and_import", "test", torrent=first_match)

    with pytest.raises(SelectionError, match="already managed by Radarr"):
        r._journal_torrent_companions(item, qbit, tmp_path / "torrents")

    assert plex.calls == 2
    assert radarr.calls == 2


def test_single_file_torrent_companion_check_avoids_full_refresh(tmp_path: Path):
    source = tmp_path / "library" / "one.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("one")
    movie = PlexMovie("One", 2001, ExternalIds(tmdb=1), source)
    match = TorrentMatch(
        "main", "samehash", "one", source, source.parent, 1.0, Path("one.mkv")
    )

    class CountingPlex(FakePlex):
        def __init__(self, movies):
            super().__init__(movies)
            self.calls = 0

        def movies(self):
            self.calls += 1
            return super().movies()

    class CountingRadarr(FakeRadarr):
        def __init__(self):
            super().__init__([])
            self.calls = 0

        def movies(self):
            self.calls += 1
            return super().movies()

    class SingleFileQbit(FakeQbit):
        def matches_for_hash(self, torrent_hash):
            return [match]

    plex = CountingPlex([movie])
    radarr = CountingRadarr()
    qbit = SingleFileQbit(
        "main",
        {source: [match]},
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=plex,
        radarr=radarr,
        qbits=[qbit],
        state=StateStore(tmp_path / "state.json"),
    )

    r._journal_torrent_companions(item=PlanItem(movie, "relocate_and_import", "test", torrent=match), qbit=qbit, local_relocation_root=tmp_path / "torrents")

    assert plex.calls == 0
    assert radarr.calls == 0


def test_single_file_torrent_revalidates_radarr_before_relocation(tmp_path: Path):
    source = tmp_path / "library" / "one.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("one")
    movie = PlexMovie("One", 2001, ExternalIds(tmdb=1), source)
    match = TorrentMatch(
        "main", "samehash", "one", source, source.parent, 1.0, Path("one.mkv")
    )

    class SingleFileQbit(FakeQbit):
        def matches_for_hash(self, torrent_hash):
            return [match]

    class ChangingRadarr(FakeRadarr):
        def __init__(self):
            super().__init__([{"id": 7, "tmdbId": 1, "hasFile": True}])
            self.calls = 0

        def movies(self):
            self.calls += 1
            return super().movies()

    radarr = ChangingRadarr()
    qbit = SingleFileQbit(
        "main",
        {source: [match]},
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=radarr,
        qbits=[qbit],
        state=StateStore(tmp_path / "state.json"),
    )

    item = PlanItem(movie, "relocate_and_import", "test", torrent=match)

    with pytest.raises(SelectionError, match="already managed by Radarr"):
        r._journal_torrent_companions(item, qbit, tmp_path / "torrents")

    assert radarr.calls == 1


def test_single_file_torrent_refreshes_missing_radarr_movie_id(tmp_path: Path):
    source = tmp_path / "library" / "one.mkv"
    source.parent.mkdir(parents=True)
    source.write_text("one")
    movie = PlexMovie("One", 2001, ExternalIds(tmdb=1), source)
    match = TorrentMatch(
        "main", "samehash", "one", source, source.parent, 1.0, Path("one.mkv")
    )

    class SingleFileQbit(FakeQbit):
        def matches_for_hash(self, torrent_hash):
            return [match]

    radarr = FakeRadarr([{"id": 7, "tmdbId": 1, "hasFile": False}])
    qbit = SingleFileQbit(
        "main",
        {source: [match]},
        relocation_root=str(tmp_path / "torrents"),
    )
    r = Reconciler(
        config(),
        plex=FakePlex([movie]),
        radarr=radarr,
        qbits=[qbit],
        state=StateStore(tmp_path / "state.json"),
    )

    item = PlanItem(movie, "relocate_and_import", "test", torrent=match)
    r._journal_torrent_companions(item, qbit, tmp_path / "torrents")

    assert item.radarr_movie_id == 7
