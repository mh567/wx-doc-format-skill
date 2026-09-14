# 编排暂留在 stage executor

## 状态

已接受，适用于 execution-v5 本轮。

## 背景

AC-1 已把复核候选接入已注册子计划，但候选重处理的编排仍留在 `native_stages._native_reviewer`（约 380 行）与 `_native_candidate_set` 内，它们会重建 plan 并重新进入 `registry.execute`。2026-09-14 架构复审将「把编排提到 kernel 或 registry」列为候选 FU-1。

## 决策

本轮不提取该编排，保留在 executor 内，并作为 FU-1 推迟。

## 理由

该改动是本轮最大的一处，且紧邻刚刚定案的 AC-1。先完成 locality 与 seam 的低风险收敛，再单独一轮处理编排，避免与已定案设计相撞。

## 后果

- 后续架构复审不应重复提议该提取，除非 FU-1 被显式开启。
- executor 仍承担 orchestration，depth 的提升留待 FU-1。
