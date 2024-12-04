import polars as pl
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# Loading in the raw data and replacing the string 'NULL' with an actual null value
data = pl.read_csv('Data/SecurityData.csv').with_columns(pl.when(pl.col('coupon_rate') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('coupon_rate')).alias('coupon_rate'),
                                                         pl.when(pl.col('spread') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('spread')).alias('spread'),
                                                         pl.when(pl.col('closing_price') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('closing_price')).alias('closing_price'),
                                                         pl.when(pl.col('current_yield') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('current_yield')).alias('current_yield'),
                                                         pl.when(pl.col('ytm') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('ytm')).alias('ytm'),
                                                         pl.when(pl.col('credit_rating') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('credit_rating')).alias('credit_rating'),
                                                         pl.when(pl.col('duration') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('duration')).alias('duration'),
                                                         pl.when(pl.col('maturity_date') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('maturity_date')).alias('maturity_date'),
                                                         pl.when(pl.col('next_call_date') == 'NULL')
                                                           .then(None)
                                                           .otherwise(pl.col('next_call_date')).alias('next_call_date'))

# Converting the data to the correct data type since they all came as strings
data = data.with_columns(pl.col('Date').str.strptime(pl.Date, format = '%m/%d/%Y'),
                         pl.col('coupon_rate').cast(pl.Float32) / 100,
                         pl.col('spread').cast(pl.Float32) / 10000,
                         pl.col('closing_price').cast(pl.Float32),
                         pl.col('current_yield').cast(pl.Float32) / 100,
                         pl.col('ytm').cast(pl.Float32) / 100,
                         pl.col('duration').cast(pl.Float32),
                         pl.col('maturity_date').str.strptime(pl.Date, format = '%m/%d/%Y'),
                         pl.col('next_call_date').str.strptime(pl.Date, format = '%m/%d/%Y'))


# Filtering out when there is no closing price, credit rating NR, no ytm, and no duration
data = data.filter(pl.col('closing_price').is_not_null(),
                   pl.col('credit_rating') != 'NR',
                   pl.col('ytm').is_not_null(),
                   pl.col('duration').is_not_null())

# Backfilling missing coupon rates
data = data.with_columns((pl.when(pl.col('CUSIP') == '12596TAC5').then(2.52).otherwise('coupon_rate')).alias('coupon_rate'))
# Re-calculating the current yield because the data sometimes incorrectly had 0 instead of Null
# And calculating the modified duration assuming semiannual compounding
data = data.with_columns((100 * pl.col('coupon_rate') / pl.col('closing_price')).alias('current_yield'),
                         (pl.col('duration') / (1 + (pl.col('ytm') / 100) / 2)).alias('modified_duration'))

duplicates = (data.group_by(['CUSIP', 'Date']).agg(pl.count()).filter(pl.col('count') > 1))
duplicates_with_data = (data.join(duplicates, on=['CUSIP', 'Date']))
duplicates_sorted = (duplicates_with_data.sort(['CUSIP', 'Date']))
unique = duplicates_sorted.unique(subset = ['Date', 'CUSIP'], keep = "last").drop('count')
data = data.join(unique, on=['Date', 'CUSIP', 'spread', 'modified_duration'], how="anti")

data = data.sort('Date').with_columns((pl.col("Date") - pl.col('Date').shift(1).over('CUSIP')).alias("time_diff"))
data = data.sort('Date').with_columns(pl.col("CUSIP").is_first_distinct().over("CUSIP").alias('is_first'))
data = data.with_columns((pl.col('time_diff') > pl.duration(days = 60)).alias('gap_exceeds'))
# Calculate an incremental counter for each cusip group based on the gap flag
data = data.with_columns(pl.col('gap_exceeds').cum_sum().over('CUSIP').alias('group_number'))
# Create the new CUSIP names by combining the original CUSIP and the group number
data = data.with_columns((pl.col('CUSIP') + "_" + pl.col('group_number').cast(pl.Utf8)).alias('cusip_renamed'))
data = data.with_columns(pl.when(pl.col('cusip_renamed').is_null()).then(pl.col('CUSIP') + '_0').otherwise('cusip_renamed').alias('CUSIP'))
data = data.drop('time_diff', 'is_first', 'gap_exceeds', 'group_number', 'cusip_renamed')

# Separating out if the data is a treasury or not
# Treasuries are used for hedging and not trading instruments
treasuries = data.filter(pl.col('spread') == 0, pl.col('asset_type') == 'Government').sort('Date')
no_treasuries = data.filter(~pl.col('CUSIP').is_in(treasuries['CUSIP'].unique())).sort('Date')

# Finding the list of cusips that contain null spreads
missing_spreads = no_treasuries.filter(pl.col('spread').is_null())['CUSIP'].unique().sort()

# Determining how many data points for spread are null versus not null for each CUSIP
num_not_missing = no_treasuries.filter(pl.col('CUSIP').is_in(missing_spreads)).group_by('CUSIP').agg(pl.col('spread').is_not_null().sum().alias('not_missing')).sort('not_missing')
num_missing = no_treasuries.filter(pl.col('CUSIP').is_in(missing_spreads)).group_by('CUSIP').agg(pl.col('spread').is_null().sum().alias('missing')).sort('missing')

# Making a dataframe that has the CUSIP, number of not null spreads, and number of null spreads
missing_data = num_not_missing.join(num_missing, on = 'CUSIP', how = 'full', coalesce = True)

# If the number of datapoints that are not null is less than 4, place these cusips into a df
no_spreads = missing_data.filter(pl.col('not_missing') < 4)['CUSIP'].unique()

# Filtering out all cusips in no_spreads
no_treasuries = no_treasuries.filter(~pl.col('CUSIP').is_in(no_spreads))

# Updating missing_spreads to not include the CUSIPs within no_spreads
missing_spreads = no_treasuries.filter(pl.col('spread').is_null())['CUSIP'].unique().sort()

# Function to fill in the missing spreads via rolling average
def fill_spreads(df, window):
    df = df.with_columns(pl.col('spread').rolling_mean(window_size = window, center = True, min_periods = 1).alias('rolling_avg'))
    df = df.with_columns(pl.when(pl.col("spread").is_null())
                           .then(pl.col("rolling_avg"))
                           .otherwise(pl.col("spread"))
                           .alias("spread"))
    return df['Date', 'CUSIP', 'spread']

fixed_spreads = {}

# Iterating through each CUSIP
for cusip in missing_spreads:
    # Filtering the data to be just the desired CUSIP
    df = no_treasuries.filter(pl.col('CUSIP') == cusip)
    # Determining the rolling window size. Minimum size is 9 (one month on each end of the null value since the function is a centered window)
    window = 2 * len(df.filter(pl.col('spread').is_null())['spread']) + 1
    if window < 9:
        window = 9
    fixed_spreads[cusip] = fill_spreads(df, window)

# Putting all the fixed spreads into one dataframe
fixed_spreads = pl.concat(list(fixed_spreads.values()))

# Combining back with the original data and keeping the non-null values
no_treasuries = no_treasuries.join(fixed_spreads, on = ['Date', 'CUSIP'], how = 'left')
no_treasuries = no_treasuries.with_columns(pl.when(pl.col('spread').is_null())
                                             .then(pl.col('spread_right'))                    
                                             .otherwise(pl.col('spread'))
                                             .alias('spread')).drop(pl.col('spread_right'))

no_treasuries = no_treasuries.select('Date', 'CUSIP', 'asset_type', 'maturity_date', 'next_call_date', 'credit_rating', 
                                     'coupon_rate', 'closing_price', 'spread', 'current_yield', 'ytm', 'duration', 'modified_duration')
treasuries = treasuries.select('Date', 'CUSIP', 'asset_type', 'maturity_date', 'next_call_date', 'credit_rating', 
                               'coupon_rate', 'closing_price', 'spread', 'current_yield', 'ytm', 'duration', 'modified_duration')


# Defining the date window we are trading on with a 6 month initialization period
trade_end = data['Date'].unique()[-1]
trade_start = trade_end - relativedelta(years = 2) - relativedelta(months = 6)

# Filtering the data to be just over the trading dates
trading_data = no_treasuries.filter(pl.col('Date') >= trade_start).sort('Date')
trading_treasuries = treasuries.filter(pl.col('Date') >= trade_start).sort('Date')

# Making a list of the CUSIPs we will be trading
trading_cusips = trading_data['CUSIP'].unique().sort()
treasury_cusips = trading_treasuries['CUSIP'].unique().sort()

# Saving to excel
no_treasuries.to_pandas().to_excel('Data/CleanData.xlsx', index = False)
trading_data.to_pandas().to_excel('Data/TradingData.xlsx', index = False)
treasuries.to_pandas().to_excel('Data/Treasuries.xlsx', index = False)
trading_treasuries.to_pandas().to_excel('Data/TradingTreasuries.xlsx', index = False)