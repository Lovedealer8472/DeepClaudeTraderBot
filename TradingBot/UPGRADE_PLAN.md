# GreenUniRabbit → Pro-Tier Upgrade Plan

## Current State Analysis

### Critical Issues Identified:
1. **Exit Pipeline**: Multiple exit paths, potential duplicates, scattered logic
2. **UI Instability**: Flickering, print interference, non-deterministic updates
3. **Log Explosions**: Excessive logging, no rotation, DEBUG spam
4. **State Sync Issues**: PnL desync, ghost positions, partial exit duplication
5. **Scan Loop Complexity**: Overly complex, non-deterministic behavior
6. **Overnight Stability**: Crashes after 12+ hours

### Architecture Goals:
- **Modular**: Clear separation of concerns
- **Deterministic**: No random behavior, predictable execution
- **Stable**: 12+ hour runs without crashes
- **Performant**: Optimized loops, minimal overhead
- **Professional**: Production-ready, well-documented

---

## PHASE 1: Architecture Cleanup ✅ IN PROGRESS

### A. Module Structure Created:
```
/app
  /core       → state, positions, signals, risk
  /engine     → scanner, executor, exit pipeline
  /ui         → UI v2, print interception
  /util       → time, math, helpers
  /data       → exporters, historical, caching
```

### B. Next Steps:
- [ ] Create core/state.py (BotState management)
- [ ] Create core/positions.py (PositionRegistry)
- [ ] Create core/signals.py (SignalRegistry)
- [ ] Create core/risk.py (RiskManager)
- [ ] Create engine/scanner.py (EngineScanner)
- [ ] Create engine/executor.py (OrderExecutor)
- [ ] Create ui/adapter.py (UIAdapter - print interception)
- [ ] Create ui/renderer.py (UIRenderer - static UI v2)

---

## PHASE 2: Exit Pipeline Rebuild ✅ STARTED

### Status:
- ✅ Created `app/engine/exit_pipeline.py` - Unified ExitPipeline class
- ✅ Single entry point for all exits
- ✅ Priority-based queue
- ✅ Validation and state management

### Next Steps:
- [ ] Integrate ExitPipeline into bot.py
- [ ] Route all exits through ExitPipeline
- [ ] Remove duplicate exit logic
- [ ] Test partial exits
- [ ] Test full exits
- [ ] Test PRS exits

---

## PHASE 3: PRS Engine Upgrade

### Requirements:
- Rate-limited (cooldown enforced)
- Data freshness checks
- One action per deterioration event
- Reset on recovery
- Never block exit pipeline

### Implementation:
- [ ] Create core/recovery.py (RecoveryModule)
- [ ] Add guardrails (stale data, missing indicators, tiny positions)
- [ ] Integrate with ExitPipeline
- [ ] Test PRS behavior

---

## PHASE 4: UI Stability

### Requirements:
- No flickering
- Static updates only
- Print interception
- Rate throttling (4-8 FPS)

### Implementation:
- [ ] Create ui/adapter.py (UIAdapter)
- [ ] Intercept all prints
- [ ] Route to file logger
- [ ] Implement rate throttling
- [ ] Test UI stability

---

## PHASE 5: Log Slimming

### Requirements:
- Rotating logs (50-100MB, 5 files)
- DEBUG mode for verbose logs
- Compact trade logs (one line per event)
- Remove spam

### Implementation:
- [ ] Update logger.py (already has rotation)
- [ ] Move verbose logs to DEBUG
- [ ] Compact trade logging
- [ ] Remove repeated debug spam

---

## PHASE 6: Performance Optimization

### Requirements:
- Cache equity once per cycle
- Cache symbol metadata
- Reduce redundant indicator computation
- Performance profiler

### Implementation:
- [ ] Add performance profiler
- [ ] Cache optimizations
- [ ] Measure improvements

---

## PHASE 7: Nightly Test Harness

### Requirements:
- Run bot N=20 times
- Each run 200-500 loops
- Record crashes, exceptions, positions, PRS actions
- CSV summary

### Implementation:
- [ ] Create devtools/stability_test.py
- [ ] Run tests
- [ ] Generate report

---

## PHASE 8: Premium Preparation

### Requirements:
- README_PRO.md
- Packager
- Test data

### Implementation:
- [ ] Create documentation
- [ ] Create packager script
- [ ] Generate test data

---

## PHASE 9: Final Report

### Deliverables:
- reports/UPGRADE_SUMMARY.md
- Stability metrics
- Performance improvements
- Remaining weaknesses
- Next steps

---

## Implementation Priority

1. **CRITICAL**: Exit Pipeline (Phase 2) - Fixes duplicate exits, state sync
2. **CRITICAL**: UI Stability (Phase 4) - Fixes flickering, print interference
3. **HIGH**: PRS Engine (Phase 3) - Makes PRS safe and reliable
4. **HIGH**: Log Slimming (Phase 5) - Reduces log volume
5. **MEDIUM**: Architecture Cleanup (Phase 1) - Improves maintainability
6. **MEDIUM**: Performance (Phase 6) - Optimizes loops
7. **LOW**: Test Harness (Phase 7) - Validation tool
8. **LOW**: Premium Prep (Phase 8) - Documentation

---

## Progress Tracking

- [x] Phase 1: Module structure created
- [x] Phase 2: ExitPipeline class created
- [ ] Phase 2: ExitPipeline integrated
- [ ] Phase 3: PRS Engine upgraded
- [ ] Phase 4: UI Stability fixed
- [ ] Phase 5: Log Slimming complete
- [ ] Phase 6: Performance optimized
- [ ] Phase 7: Test harness created
- [ ] Phase 8: Premium prep complete
- [ ] Phase 9: Final report generated

---

**Last Updated**: 2025-11-15
**Status**: Phase 1-2 in progress

