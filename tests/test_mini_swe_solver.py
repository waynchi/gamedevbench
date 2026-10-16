from gamedevbench.src.mini_swe_solver import MiniSweSolver


def test_mini_swe_rate_limit_detection_ignores_429_in_request_id():
    assert MiniSweSolver.is_rate_limit_error("Error code: 429")
    assert not MiniSweSolver.is_rate_limit_error(
        "Internal server error (request_id=req_abc429def)"
    )
