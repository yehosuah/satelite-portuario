from datetime import datetime, timedelta, timezone

import pytest

from puertogt.ml.change import TemporalEvidence, build_siamese_change_model, track_change


def evidence(name, days, changed, **kwargs):
    return TemporalEvidence(observation_id=name, captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days),
                            changed=changed, **kwargs)


GEOMETRY = {"type": "Polygon", "coordinates": [[[-90.79, 13.92], [-90.78, 13.92], [-90.78, 13.93], [-90.79, 13.93], [-90.79, 13.92]]]}


def test_t0_t1_t2_requires_second_valid_changed_observation():
    t0, t1 = evidence("t0", 0, False), evidence("t1", 10, True)
    candidate = track_change(t0, [t1], geometry=GEOMETRY, model_id="changes-v1")
    assert candidate.status == "candidate"
    cloudy = evidence("cloudy", 20, True, observable=False)
    still_candidate = track_change(t0, [t1, cloudy], geometry=GEOMETRY, model_id="changes-v1")
    assert still_candidate.status == "candidate"
    assert still_candidate.confirming_observation_id is None
    confirmed = track_change(t0, [t1, cloudy, evidence("t2", 30, True)], geometry=GEOMETRY, model_id="changes-v1")
    assert confirmed.status == "persistent"
    assert confirmed.confirming_observation_id == "t2"
    assert confirmed.interval_start == t0.captured_at
    assert confirmed.interval_end == t1.captured_at


def test_negative_pair_and_reversal_keep_history():
    t0 = evidence("t0", 0, False)
    assert track_change(t0, [evidence("still-unchanged", 5, False)], geometry=GEOMETRY, model_id="m") is None
    event = track_change(t0, [evidence("t1", 10, True), evidence("t2", 20, True),
                              evidence("cloudy", 30, False, observable=False), evidence("reversed", 40, False)],
                         geometry=GEOMETRY, model_id="m")
    assert event.status == "reverted"
    assert event.confirming_observation_id == "t2"
    assert [row["state"] for row in event.history if row.get("transition")] == ["candidate", "persistent", "reverted"]


def test_radar_or_duplicate_acquisition_cannot_confirm_optical_change():
    t0, t1 = evidence("t0", 0, False), evidence("t1", 10, True)
    event = track_change(t0, [t1, evidence("radar", 20, True, sensor="sentinel-1")], geometry=GEOMETRY, model_id="m")
    assert event.status == "candidate"
    with pytest.raises(ValueError, match="distinct"):
        track_change(t0, [t1, evidence("other-representation", 10, True)], geometry=GEOMETRY, model_id="m")


def test_siamese_network_updates_decoder_and_keeps_encoder_frozen():
    torch = pytest.importorskip("torch")
    class Encoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Conv2d(3, 4, 3, padding=1)
        def forward(self, value):
            return [self.layer(value)]
    encoder = Encoder()
    network = build_siamese_change_model(encoder, [4], decoder_channels=8)
    before = encoder.layer.weight.detach().clone()
    optimizer = torch.optim.AdamW([p for p in network.parameters() if p.requires_grad], lr=0.01)
    network.train()
    output = network(torch.zeros(1, 3, 8, 8), torch.ones(1, 3, 8, 8))
    assert output.shape == (1, 1, 8, 8)
    loss = torch.nn.functional.binary_cross_entropy_with_logits(output, torch.ones_like(output))
    loss.backward()
    optimizer.step()
    assert torch.equal(before, encoder.layer.weight)
    assert encoder.layer.weight.grad is None
    assert not encoder.training
