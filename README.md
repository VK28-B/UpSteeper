# UpSteeper

UpSteeper v3.1.0 is a local Windows-first productivity app with:
- Daily tasks
- Long-term goals
- Earned access rules
- YouTube hosts-file blocking
- Temporary incognito rewards
- Monthly analytics
- Animated dark UI

## Run

```bash
pip install -r requirements.txt
python main.py
```

## Notes

- YouTube blocking and browser policy control require Windows administrator rights.
- On non-Windows systems, those features stay in a safe fallback mode.
- The database lives in `data/upsteeper.db`.
- Monthly charts are generated into `generated/monthly_chart.png`.
