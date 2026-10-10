# Adopted reconstruction boundaries for reproducible experiments

`laozhuang_delivery_seed.csv` is the adopted cold-start delivery-5% reconstruction used to reproduce the PID/RK historical comparison. `laozhuang_reconstructed_diversion_target.csv` freezes the adopted PID/RK reconstruction as the diversion target for the 1.4 m control experiment. Both contain 168 hourly records for 2026-04-14 through 2026-04-21.

These are simulation inputs derived from previously accepted reconstruction results, not measured diversion records. The columns preserve timestamps, original flow anchors, allowed bounds, source flow, openings and prior depth values used by the refinement adapter. They are deliberately stored under input so a fresh checkout does not depend on archived output CSVs. The prior h_sim/h_target values are provenance and refinement metadata; the setpoint controller uses its explicit 1.4 m target and DLL simulated state.

Source outputs were `output/laozhuang_delivery_5pct/reconstruction_audit.csv` and `output/laozhuang_pid_rk_mpc/reconstruction_audit.csv`. Generated output audits and models are not versioned; output images are versioned. Running the scripts recreates local audits needed for plotting and numerical checks.
