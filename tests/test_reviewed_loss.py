import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchvision")

from torchvision.models.detection.anchor_utils import AnchorGenerator  # noqa: E402
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor, TwoMLPHead  # noqa: E402
from torchvision.models.detection.image_list import ImageList  # noqa: E402
from torchvision.models.detection.roi_heads import RoIHeads, fastrcnn_loss  # noqa: E402
from torchvision.models.detection.rpn import RegionProposalNetwork, RPNHead  # noqa: E402
from torchvision.ops import MultiScaleRoIAlign  # noqa: E402

from puertogt.ml.detection import Detector, reviewed_loss_scope  # noqa: E402


def real_heads():
    rpn = RegionProposalNetwork(
        AnchorGenerator(sizes=((16,),), aspect_ratios=((1.0,),)), RPNHead(1, 1),
        0.7, 0.3, 256, 0.5, {"training": 100, "testing": 100},
        {"training": 100, "testing": 100}, 0.7,
    )
    roi = RoIHeads(MultiScaleRoIAlign(["0"], 2, 2), TwoMLPHead(4, 8),
                   FastRCNNPredictor(8, 2), 0.5, 0.5, 512, 0.25, None, 0.05, 0.5, 100)
    head = torch.nn.Module()
    head.rpn, head.roi_heads = rpn, roi
    return head


def test_actual_rpn_and_roi_ignore_unknown_footprints_and_preserve_boundary_positives():
    head = real_heads()
    mask = np.zeros((32, 32), dtype=bool)
    mask[:, :16] = True
    # Interior negative, unknown negative, straddling negative, known partial
    # positive (center x15), and a negative overlapping image padding.
    boxes = torch.tensor([[1., 1., 5., 5.], [24., 1., 28., 5.],
                          [14., 1., 18., 5.], [12., 20., 18., 24.], [-2., 8., 2., 12.]])
    target = {"boxes": boxes[3:4], "labels": torch.ones(1, dtype=torch.int64)}
    with reviewed_loss_scope(head, mask):
        rpn_labels, _ = head.rpn.assign_targets_to_anchors([boxes], [target])
        assert rpn_labels[0].tolist() == [0, -1, -1, 1, -1]
        objectness = torch.zeros(5, requires_grad=True)
        deltas = torch.ones((5, 4), requires_grad=True)
        rpn_losses = head.rpn.compute_loss(objectness, deltas, rpn_labels, [torch.zeros((5, 4))])
        sum(rpn_losses).backward()
        assert objectness.grad[[1, 2, 4]].tolist() == [0, 0, 0]
        assert objectness.grad[0] > 0 and objectness.grad[3] < 0
        assert deltas.grad[3].abs().sum() > 0
        _, roi_labels = head.roi_heads.assign_targets_to_proposals([boxes], [target["boxes"]], [target["labels"]])
        assert roi_labels[0].tolist() == [0, -1, -1, 1, -1]
        sampled = head.roi_heads.subsample(roi_labels)[0]
        logits = torch.zeros((5, 2), requires_grad=True)
        regression = torch.ones((5, 8), requires_grad=True)
        roi_losses = fastrcnn_loss(logits[sampled], regression[sampled],
                                  [roi_labels[0][sampled]], [torch.zeros((len(sampled), 4))])
        sum(roi_losses).backward()
        assert logits.grad[[1, 2, 4]].abs().sum() == 0
        assert logits.grad[[0, 3]].abs().sum() > 0
        assert regression.grad[3].abs().sum() > 0
    assert "assign_targets_to_anchors" not in head.rpn.__dict__
    assert "assign_targets_to_proposals" not in head.roi_heads.__dict__
    assert head.rpn.assign_targets_to_anchors([boxes], [target])[0][0].tolist() == [0, 0, 0, 1, 0]


def test_hooks_restore_after_exception_and_do_not_change_other_instances():
    head, other = real_heads(), real_heads()
    methods = [(head.rpn, "assign_targets_to_anchors"), (head.rpn, "compute_loss"),
               (head.roi_heads, "assign_targets_to_proposals"),
               (head.roi_heads, "select_training_samples"), (head.roi_heads, "forward")]
    originals = [getattr(obj, name) for obj, name in methods]
    with pytest.raises(RuntimeError, match="intentional"):
        with reviewed_loss_scope(head, np.ones((32, 32), dtype=bool)):
            assert "assign_targets_to_anchors" not in other.rpn.__dict__
            assert "assign_targets_to_proposals" not in other.roi_heads.__dict__
            raise RuntimeError("intentional")
    assert [getattr(obj, name) for obj, name in methods] == originals
    assert all(name not in obj.__dict__ for obj, name in methods)


def test_empty_supervision_has_finite_zero_loss_and_zero_gradients():
    head = real_heads()
    mask = np.zeros((32, 32), dtype=bool)
    mask[0, 0] = True
    boxes = torch.tensor([[8., 8., 16., 16.]])
    targets = [{"boxes": torch.empty((0, 4)), "labels": torch.empty(0, dtype=torch.int64)}]
    with reviewed_loss_scope(head, mask) as counts:
        labels, _ = head.rpn.assign_targets_to_anchors([boxes], targets)
        logits = torch.zeros(1, requires_grad=True)
        deltas = torch.ones((1, 4), requires_grad=True)
        rpn_losses = head.rpn.compute_loss(logits, deltas, labels, [torch.zeros((1, 4))])
        features = {"0": torch.ones((1, 1, 8, 8), requires_grad=True)}
        _, roi_losses = head.roi_heads(features, [boxes], [(32, 32)], targets)
        loss = sum(rpn_losses) + sum(roi_losses.values())
        assert torch.isfinite(loss) and loss == 0
        loss.backward()
        assert logits.grad.abs().sum() == deltas.grad.abs().sum() == features["0"].grad.abs().sum() == 0
        assert counts["rpn_supervised"] == counts["roi_supervised"] == 0


def test_detector_step_passes_scope_to_real_heads_and_skips_empty_updates():
    from types import SimpleNamespace

    class SmallNetwork(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = torch.nn.ModuleList([torch.nn.Conv2d(1, 1, 1), torch.nn.Conv2d(1, 1, 1)])
            self.decoders = torch.nn.ModuleDict({"detect": torch.nn.ModuleList([real_heads()])})

        def forward(self, image, targets):
            tensor = torch.from_numpy(image)[None]
            features = {"0": self.encoder[1](self.encoder[0](tensor))}
            images = ImageList(tensor, [image.shape[-2:]])
            head = self.decoders["detect"][-1]
            references = [targets[0]["detect"]]
            proposals, rpn_losses = head.rpn(images, features, references)
            _, roi_losses = head.roi_heads(features, proposals, images.image_sizes, references)
            return SimpleNamespace(loss_dict={**rpn_losses, **roi_losses})

    torch.manual_seed(5)
    network = SmallNetwork()
    detector = Detector(None, network, "cpu")
    detector._context = lambda image: image
    optimizer = detector.make_optimizer()
    image = np.ones((1, 32, 32), dtype=np.float32)
    mask = np.zeros((32, 32), dtype=bool)
    mask[:, :16] = True
    result = detector.frozen_train_step(image, np.array([[5, 5, 11, 11]], dtype=np.float32),
                                        reviewed_mask=mask, optimizer=optimizer)
    assert result["updated_parameters"] and result["supervision"]["roi_supervised"] > 0
    assert result["encoder_frozen"] and not network.training
    before = {name: value.detach().clone() for name, value in network.state_dict().items()}
    mask[:] = False
    mask[0, 0] = True
    skipped = detector.frozen_train_step(image, np.empty((0, 4), dtype=np.float32),
                                         reviewed_mask=mask, optimizer=optimizer)
    assert skipped["skipped"] and not skipped["updated_parameters"]
    assert all(torch.equal(before[name], value) for name, value in network.state_dict().items())
    assert not network.training
