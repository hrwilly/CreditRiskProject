import polars as pl

def read_and_clean_price(file_path, type):
    price = pl.read_excel(file_path, sheet_id = 2)

    price = price.sort('Date').fill_null(strategy="backward").with_columns(pl.lit(type).alias('Type'))

    if type == 'IG':
        price = price.melt(id_vars=["Date", "Type"], 
                           value_vars=["3Y_price", "5Y_price", "7Y_price", "10Y_price"],
                           variable_name="Tenor",
                           value_name="Price")
    else:
        price = price.melt(id_vars=["Date", "Type"], 
                           value_vars=["3Y_price", "5Y_price", "7Y_price"],
                           variable_name="Tenor",
                           value_name="Price")

    price = price.with_columns(pl.col("Tenor").str.extract(r"(\d+)").cast(pl.Int64).alias("Tenor"))

    return price

IG_price = read_and_clean_price('Data/CDX IG Price.xlsx', 'IG')
HY_price = read_and_clean_price('Data/CDX HY Price.xlsx', 'HY')

price = IG_price.join(HY_price, on = ['Date', 'Type', 'Tenor', 'Price'], how = 'full', coalesce = True).sort('Date')

def read_and_clean_spread(filepath, type):
    spread = pl.read_excel(filepath).with_columns(pl.lit(type).alias('Type'))

    spread = spread.sort('Date').fill_null(strategy="backward").with_columns(pl.lit(type).alias('Type'))

    spread = spread.melt(id_vars=["Date", "Type"], 
                           value_vars=["3Y", "5Y", "7Y", "10Y"],
                           variable_name="Tenor",
                           value_name="Spread")

    return spread.with_columns(pl.col("Tenor").str.extract(r"(\d+)").cast(pl.Int64).alias("Tenor"))



IG_spread = read_and_clean_spread('Data/CDX IG Spread.xlsx', 'IG')
HY_spread = read_and_clean_spread('Data/CDX HY Spread.xlsx', 'HY')

spread = IG_spread.join(HY_spread, on = ['Date', 'Type', 'Tenor', 'Spread'], how = 'full', coalesce = True).sort('Date')

data = price.join(spread, on = ['Date', 'Type', 'Tenor'], how = 'full', coalesce = True).drop_nulls()

data = data.with_columns(((pl.col('Price') - pl.col('Price').shift(1).over('Type', 'Tenor')) / ((pl.col('Spread')) - (pl.col('Spread').shift(1).over('Type', 'Tenor')))).alias('dP/dS'))
data = data.with_columns((pl.col('dP/dS') / pl.col('Price')).alias('Spread_Duration')).drop_nulls().drop('dP/dS')
data = data.filter(pl.col('Spread_Duration').is_finite())
data = data.with_columns(pl.col('Spread') / 10000)

data.to_pandas().to_excel('Data/TradingCDS.xlsx', index = False)