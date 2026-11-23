# MandiMitra Historical Rolling-Origin Backtest Report

## Overall Performance Summary

- **Total Evaluated Windows**: 9805
- **Decisive Calls (SELL NOW / WAIT)**: 7080 (NEUTRAL coverage: 27.79%)
- **MandiMitra Decisive Accuracy**: 63.25%
- **WAIT Precision**: 47.63% (590 WAIT calls, avg 14d move: +2.56%)
- **SELL NOW Precision**: 64.67% (6490 SELL calls, avg 14d move: +0.23%)
- **Always SELL NOW Baseline Accuracy (all rows)**: 63.39%
- **Always SELL NOW Baseline (matched decisive rows)**: 63.64%
- **Momentum-Only Baseline Accuracy**: 50.84% (WAIT precision: 35.04%)

## Perishable vs Storable Breakdown

crop_group  storable  total_cases  decisive_cases  accuracy_pct  wait_precision_pct  neutral_coverage_pct  avg_wait_move_pct  always_sell_acc_pct  momentum_acc_pct
Perishable     False         3498            2543         60.32               55.39                 27.30               3.64                57.92             51.93
  Storable      True         6307            4537         64.89               41.12                 28.06               1.65                66.42             50.05

## Commodity Performance Breakdown

         commodity  storable  total_cases  decisive_cases  accuracy_pct  wait_precision_pct  neutral_coverage_pct  avg_wait_move_pct  always_sell_acc_pct  momentum_acc_pct
           Mustard      True          477             330         69.39               27.27                 30.82               1.32                68.97             58.36
              Rice      True          530             356         69.38               26.67                 32.83               0.68                70.94             50.38
             Wheat      True          795             572         68.18               30.77                 28.05               0.09                70.31             50.60
            Garlic      True          530             391         66.75               59.57                 26.23               3.15                62.64             53.79
  Pigeon Pea (Tur)      True          424             303         66.67               57.89                 28.54               2.94                66.27             54.58
             Maize      True          530             400         65.00               29.17                 24.53              -0.08                69.06             50.00
          Chickpea      True          477             345         64.64               43.48                 27.67               1.45                67.71             45.63
         Groundnut      True          530             390         64.62               47.62                 26.42               1.67                65.09             48.80
           Soybean      True          530             379         64.12               29.63                 28.49               0.26                65.85             50.47
             Onion     False          742             541         63.77               66.67                 27.09               4.83                57.68             56.79
          Turmeric      True          530             379         61.74               37.04                 28.49               1.41                64.15             48.44
           Brinjal     False          530             381         60.89               68.75                 28.11               6.78                58.11             48.68
           Cabbage     False          477             349         60.46               51.61                 26.83               2.84                58.91             49.47
            Tomato     False          742             532         59.59               57.14                 28.30               4.60                56.33             52.29
      Green Chilli     False          530             394         59.39               46.15                 25.66               1.32                59.25             53.24
Green Gram (Moong)      True          424             298         58.39               30.43                 29.72               2.38                63.21             42.75
            Potato      True          530             394         58.12               44.83                 25.66               2.30                60.57             47.31
       Cauliflower     False          477             346         56.36               35.71                 27.46               0.74                58.07             48.21