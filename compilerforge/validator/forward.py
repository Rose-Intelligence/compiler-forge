"""One evaluation round.

The order is the security argument, and it must not be rearranged:

1. Freeze the artifact set by reading commitments at a pinned block.
2. Wait for a *later* block and draw its hash.
3. Derive the round's tasks from that hash.

Because step 2 produces entropy that did not exist at step 1, an artifact cannot
have been tuned to the tasks it is about to be evaluated on — and a third party
can check that from public data rather than taking anyone's word for it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from compilerforge.chain.access import ChainError, MetagraphSnapshot
from compilerforge.chain.audit import AuditRepository, RoundBundle
from compilerforge.chain.commitments import FrozenArtifact, earliest_commitment_times
from compilerforge.corpus.package import Corpus
from compilerforge.evaluation.build import toolchain_digest
from compilerforge.evaluation.selection import RoundSeed, SelectionError, derive_round
from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.sandbox.runner import ArtifactError, ArtifactRunner
from compilerforge.spec import SPEC
from compilerforge.utils.logging import logger
from compilerforge.utils.misc import short_digest
from compilerforge.validator.round import (
    ProducedPatch,
    RoundRunner,
    assign_producers,
    build_aggregator,
)


class RoundAborted(RuntimeError):
    """The round could not run. No weights are emitted, and the reason is stated."""


async def forward(self) -> RoundBundle | None:
    """Run one full round and publish its audit bundle.

    Returns ``None`` only when the round legitimately had nothing to score — no
    commitments yet. Anything else that prevents a round raises
    :class:`RoundAborted`, because "produced no weights" and "silently did
    nothing" must not look the same in a log.
    """
    self.round_number += 1
    logger.info(f"=== Round {self.round_number} ===")

    corpus = Corpus.load(self.corpus_dir, self.config.corpus.snapshot)
    if not corpus.packages:
        raise RoundAborted(f"no task packages under {self.corpus_dir}")

    digest = toolchain_digest()

    # -- freeze the artifact set -------------------------------------
    # v11 returns commitments with the metagraph, so the neuron table and the
    # artifact set are one read at one block rather than two that could observe
    # different chain states.
    try:
        snapshot = self.chain.metagraph()
    except ChainError as exc:
        raise RoundAborted(f"cannot read the metagraph: {exc}") from exc

    self.metagraph = snapshot
    freeze_block = snapshot.block
    artifacts = freeze_artifacts(snapshot)
    if not artifacts:
        logger.warning(f"No artifact commitments at block {freeze_block}; nothing to evaluate")
        return None
    logger.info(f"Froze {len(artifacts)} artifacts at block {freeze_block}")

    # -- draw entropy that did not exist at freeze time ---------------
    select_block = freeze_block + self.config.neuron.freeze_lead_blocks
    await wait_for_block(self, select_block)

    try:
        block_hash = self.chain.block_hash(select_block)
    except ChainError as exc:
        raise RoundAborted(f"cannot draw the task-selecting hash: {exc}") from exc

    seed = RoundSeed(
        block_number=select_block,
        block_hash=block_hash,
        corpus_snapshot=corpus.snapshot_id,
        spec_digest=SPEC.digest(),
    )

    try:
        plan = derive_round(
            seed=seed,
            corpus=corpus,
            toolchain_digest=digest,
            public_task_count=self.config.neuron.public_tasks,
            hidden_task_count=self.config.neuron.hidden_tasks,
        )
    except SelectionError as exc:
        raise RoundAborted(f"cannot derive round: {exc}") from exc

    logger.info(
        f"{len(plan.public_tasks)} public + {len(plan.hidden_tasks)} hidden tasks "
        f"from block {select_block}"
    )

    # -- production once ----------------------------------------------
    assignments = assign_producers(plan, artifacts)
    mine = [a for a in assignments if a.producer_uid == self.uid]
    logger.info(f"Producing {len(mine)} of {len(assignments)} assigned pairs")
    patches = await produce_patches(self, mine, plan, artifacts)

    # -- verification everywhere --------------------------------------
    runner = RoundRunner(ctx=self.evaluation_context(corpus), workdir=self.workdir / "rounds")
    result = runner.verify(plan, patches, round_number=self.round_number)

    for challenge in result.challenges:
        logger.error(f"Reproduction challenge: {challenge.detail}")
    if result.voided_tasks:
        logger.warning(
            f"{len(result.voided_tasks)} tasks voided; no crown change this round"
        )

    # -- aggregate and decide the crown -------------------------------
    aggregates = build_aggregator(plan, result).aggregate()
    hotkeys = {a.digest: a.hotkey for a in artifacts}
    commitments: dict[str, datetime] = earliest_commitment_times(artifacts)

    challenge = self.allocator.champions.evaluate(
        aggregates,
        commitments=commitments,
        hotkeys=hotkeys,
        round_was_noisy=result.round_was_noisy,
    )
    if challenge and challenge.dethroned:
        logger.success(
            f"Crown to {short_digest(challenge.challenger_digest)}: {challenge.reason}"
        )

    self.allocator.record_round(aggregates)

    # -- weights, one vector per mechanism ----------------------------
    generalist = self.allocator.allocate_generalist(aggregates, hotkeys=hotkeys)
    specialist = self.allocator.allocate_specialist_and_bounty(
        aggregates,
        hotkeys=hotkeys,
        commitments=commitments,
        cells=self.specialist_cells,
    )
    submitted = self.submit_round_weights(generalist, specialist)
    if not any(submitted.values()):
        # Every mechanism was refused. The round's measurements are still worth
        # publishing, but an operator must not read this as a normal round.
        logger.error(
            "No mechanism accepted this round's weights; the round produced "
            "measurements but no on-chain effect"
        )

    # -- publish everything needed to reproduce the ranking -----------
    champion = self.allocator.champions.champion
    bundle = RoundBundle(
        round_number=self.round_number,
        block_number=select_block,
        block_hash=block_hash,
        corpus_snapshot=corpus.snapshot_id,
        toolchain_digest=digest,
        task_manifest=plan.manifest(),
        score_artifacts=result.score_artifacts,
        weight_vectors=dict(self.last_weights),
        accepted_patches=result.accepted_patches,
        voided_tasks=result.voided_tasks,
        champion_digest=champion.artifact_digest if champion else None,
    )
    AuditRepository(root=self.audit_dir).publish(bundle)
    logger.info(f"Published audit bundle {bundle.bundle_hash()[:26]}")
    return bundle


def freeze_artifacts(snapshot: MetagraphSnapshot) -> list[FrozenArtifact]:
    """Turn the commitments in a metagraph read into the frozen artifact set.

    A commitment that does not parse, or that declares a different interface
    version, is skipped with a reason at debug level. Those are the miner's
    problem to fix and are expected during an interface transition; what must
    never happen is a valid artifact being dropped silently, which is why the
    count that survived is logged by the caller.
    """
    frozen: list[FrozenArtifact] = []
    uids = snapshot.hotkeys()

    for hotkey, payload in sorted(snapshot.commitments.items()):
        if not payload:
            continue
        try:
            commitment = ArtifactCommitment.decode(payload)
        except Exception as exc:  # noqa: BLE001 - a malformed commitment is the miner's problem
            logger.debug(f"Ignoring unparseable commitment from {hotkey}: {exc}")
            continue
        if commitment.iface != SPEC.interface_version:
            logger.debug(
                f"Ignoring {hotkey}: interface {commitment.iface}, "
                f"this round runs {SPEC.interface_version}"
            )
            continue
        uid = uids.get(hotkey)
        if uid is None:
            logger.debug(f"Ignoring {hotkey}: committed but not registered")
            continue
        frozen.append(
            FrozenArtifact(
                uid=uid, hotkey=hotkey, commitment=commitment, frozen_at_block=snapshot.block
            )
        )
    return frozen


async def produce_patches(self, assignments, plan, artifacts) -> list[ProducedPatch]:
    """Execute the agents assigned to this validator and publish their patches."""
    runner = ArtifactRunner(
        workdir=self.workdir / "artifacts",
        container_cli=self.config.sandbox.container_cli,
        proxy_url=self.config.sandbox.inference_proxy_url,
    )
    by_digest = {a.digest: a for a in artifacts}
    by_task = {t.task.task_id: t for t in plan.tasks}
    produced: list[ProducedPatch] = []

    for assignment in assignments:
        artifact = by_digest.get(assignment.artifact_digest)
        selected = by_task.get(assignment.task_id)
        if artifact is None or selected is None:
            logger.warning(
                f"Assignment references an unknown artifact or task "
                f"({short_digest(assignment.artifact_digest)}); skipping"
            )
            continue
        try:
            await runner.pull(artifact.commitment)
            run = await runner.run(artifact.commitment, selected.task, selected.package.repo_dir)
            runner.verify_interface(run, selected.task)
        except ArtifactError as exc:
            # An interface violation is a zero, not a negotiation. Publishing no
            # patch is exactly that, and the reason is recorded.
            logger.info(f"Artifact {short_digest(artifact.digest)}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad artifact must not end the round
            logger.exception(
                f"Artifact {short_digest(artifact.digest)} failed to run: {exc}"
            )
            continue

        if not run.produced_patch:
            # An honest empty result. Scored above a rejected one, and it still
            # qualifies for the floor pool.
            logger.debug(
                f"Artifact {short_digest(artifact.digest)} returned no patch "
                f"for {short_digest(selected.task.task_id)}"
            )
            continue

        produced.append(
            ProducedPatch(
                artifact_digest=artifact.digest,
                task_id=selected.task.task_id,
                producer_uid=assignment.producer_uid,
                patch=run.patch,
                patch_digest=run.patch_digest,
            )
        )
    return produced


async def wait_for_block(self, block: int) -> None:
    """Sleep until the chain reaches ``block``.

    Polls rather than blocking on the SDK's waiter so a long wait stays
    interruptible and reports progress.
    """
    while True:
        head = self.block
        if head >= block:
            return
        remaining = block - head
        logger.info(f"Waiting {remaining} blocks for the task-selecting hash")
        await asyncio.sleep(min(remaining * 12, 300))
