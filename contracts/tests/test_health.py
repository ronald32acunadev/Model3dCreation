from model3d_contracts.health import Health


def test_health_round_trips():
    body = {
        "ready": True,
        "mode": {"name": "single_gpu_swap", "texture": True, "reason": "One GPU with 11.0 GiB free: shape and paint take turns"},
        "gpus": [
            {
                "bus_id": "0000:29:00.0",
                "stage": "shape+paint",
                "ready": True,
                "memory_used_mib": 7800,
                "memory_total_mib": 12227,
                "memory_free_mib": 11173,
            },
            {
                "bus_id": "0000:25:00.0",
                "stage": None,
                "ready": False,
                "memory_used_mib": None,
                "memory_total_mib": None,
                "memory_free_mib": 11761,
            },
        ],
        "error": None,
    }
    assert Health.model_validate(body).model_dump(mode="json") == body
    failed = {**body, "ready": False, "error": {"code": "mode_unavailable", "message": "No NVIDIA GPU is visible to PyTorch"}}
    assert Health.model_validate(failed).error.code == "mode_unavailable"
