from gridcarbon import GridCarbon

gc = GridCarbon()
print("BAs built:", gc.bas())

e = gc.estimate("94110", annual_kwh=4800)
print("\n", e)
print("  flat would be:", round(4800 * e.kg_per_kwh_flat, 1), "kg")
for w in e.warnings:
    print("  !", w)

print("\n--- with real monthly bills ---")
bills = {1: 480, 2: 430, 3: 400, 4: 360, 5: 340, 6: 360,
         7: 420, 8: 440, 9: 420, 10: 380, 11: 400, 12: 470}
e2 = gc.estimate("94110", annual_kwh=sum(bills.values()), monthly_kwh=bills)
print(" ", e2, " confidence:", e2.confidence)

print("\n--- same annual kWh, different ZIPs ---")
for z in ["94110", "90012", "73301", "02139"]:
    r = gc.estimate(z, 4800)
    print(f"  {z}  {r.ba:5s}  {r.kg_co2e:8,.0f} kg   {r.kg_per_kwh:.4f} kg/kWh  {r.uplift_pct:+.1f}%")

print("\n--- profile for UI (CISO diurnal) ---")
p = gc.profile("94110")
rel = p.diurnal_weight / p.diurnal_weight.mean()
print("  hr  kg/kWh   load (1.0 = flat)")
for h in range(0, 24, 2):
    bar = "#" * int(rel[h] * 12)
    print(f"  {h:2d}  {p.diurnal_intensity[h]:.4f}   {rel[h]:.2f}  {bar}")
print(f"  rho={p.rho:.3f}  alpha={p.alpha:.3f}")

print("\n--- compare ---")
for k, v in gc.compare("94110", 4800).items():
    print(f"  {k}: {v}")
