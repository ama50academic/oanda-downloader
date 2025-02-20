import os
import re
import v20
import csv
import sys
import datetime as dt
from time import time
from runpy import run_path
from argparse import ArgumentParser

"""
Description: Downloads candles from oanda.com and saves them into a csv
  file. Please, refer to README.md for instructions.
Version: 1
Author: Sergei Bondarenko <sergei@bondarenko.xyz>
Website: https://bondarenko.xyz
Date: February 2019
"""

# A maximum number of candles which oanda.com can return.
MAX_API_CANDLES = 5000  

# A starting time if it is not specified. oanda.com have data only
# starting from 2002-05-07.
MIN_TIME = 1020800000

class MaxCountError(Exception):
  """Exception for errors when you ask more than `MAX_API_CANDLES`
  candles at once from oanda.com.
  """
  pass


class APIError(Exception):
  """Exception for other oanda's API errors."""
  pass


class ConnectionError(Exception):
  """Exception for Internet connection errors."""
  pass


def time_crop_fraction(time):
  """Removes a fractional part from time. For example, converts
  "1549886400.000000000" to "1549886400" and
  "2019-02-20T09:00:00.000000000Z" to "2019-02-20T09:00:00".

  Args:
    date: UNIX or RFC3339 time string.

  Returns:
    Time without a fractional part in the same format.
  """
  return re.sub('\..*', '', time)


def time_to_unix(time):
  """Converts different time formats to UNIX time.
  
  Args:
    time: UNIX or RFC3339 time.

  Returns:
    A UNIX time.
  """
  if time is not None:
    try:
      result = int(dt.datetime.strptime(time, '%Y-%m-%dT%H:%M:%S').timestamp())
    except (ValueError, TypeError):
      try:
        result = int(dt.datetime.strptime(time, '%Y-%m-%d').timestamp())
      except (ValueError, TypeError):
        result = int(time)
  else:
    result = None
  return result


def write(name, candles):
  """Writes candles into a csv file.

  Args:
    name: A path of an output file.
    candles: An array of dictionaries, each of one representing a
      candle.
  """
  with open(name, 'w') as f:
    writer = csv.DictWriter(f, fieldnames=candles[0].keys())
    writer.writeheader()
    for line in candles:
      writer.writerow(line)


def progress_bar(start, current, end):
  """Draws a progress bar.

  Args:
    start: A start time.
    current: A current processing time.
    end: An end time.

  Returns:
    A nice looking progress bar string.
  """
  percentage = (current - start) / (end - start) * 100
  bar = '#' * round(percentage / 2)
  empty = '.' * (50 - round(percentage / 2))
  return f"[{bar}{empty}] {percentage:.1f}%"


def download_batch(api, cfg):
  """Downloads one batch of candles (up to `MAX_API_CANDLES`).

  Args:
    api: Connection via v20 api.Context class.
    cfg: Parameters for an API call.

  Returns:
    An array of dictionaries, each of which representing one candle.

  Raises:
    MaxCountError: If you ask more than `MAX_API_CANDLES` candles at
      once from oanda.com.
    APIError: If there is an other API error.
    ConnectionError: If can not get a batch 5 times in a row.
  """

  kwargs = {
    'instrument': cfg['instrument'],
    'price': cfg['price'],
    'granularity': cfg['granularity'],
    'smooth': cfg['smooth'],
    'includeFirst': False,
    'dailyAlignment': cfg['daily_alignment'],
    'alignmentTimezone': cfg['alignment_timezone'],
    'weeklyAlignment': cfg['weekly_alignment'],
    'fromTime': cfg['from_time']
  }

  if 'count' in cfg:
    kwargs['count'] = cfg['count']
  else:
    kwargs['toTime'] = cfg['to_time']

  # Get batch of candles. Retry 5 times at maximum.
  for i in range(0, 5):
    try:
      response = api.instrument.candles(**kwargs)
    except (v20.errors.V20ConnectionError, v20.errors.V20Timeout):
      continue
    break
  else:
    raise ConnectionError

  if response.status != 200:
    error_message = response.body['errorMessage']
    if error_message == "Maximum value for 'count' exceeded":
      raise MaxCountError
    else:
      raise APIError(error_message)

  candles = response.body['candles']
  result = []

  for candle in candles:
    c = {
      'time': time_crop_fraction(candle.time),
      # 'complete': candle.complete,
      # 'volume': candle.volume
    }
    if 'A' in cfg['price']:
      c['openAsk'] = candle.ask.o
      c['highAsk'] = candle.ask.h
      c['lowAsk'] = candle.ask.l
      c['closeAsk'] = candle.ask.c
    if 'B' in cfg['price']:
      c['openBid'] = candle.bid.o
      c['highBid'] = candle.bid.h
      c['lowBid'] = candle.bid.l
      c['closeBid'] = candle.bid.c
    if 'M' in cfg['price']:
      c['open'] = candle.mid.o
      c['high'] = candle.mid.h
      c['low'] = candle.mid.l
      c['close'] = candle.mid.c
    # my new
    c['volume'] = candle.volume

    result.append(c)
  return result


def download(api, cfg):
  """Downloads and concatenates candles from several batches.

  Args:
    api: Connection via v20 api.Context class.
    cfg: Parameters from a config.

  Returns:
    An array of dictionaries, each of which representing one candle.
  """
  try:
    # Maybe we can download data in one batch?
    result = download_batch(api, cfg)
  except MaxCountError:
    # No, we can't. Then download and concatenate batches.
    result = []

    to_time = cfg['to_time']
    from_time = cfg['from_time']
    cfg['to_time'] = None
    cfg['count'] = MAX_API_CANDLES
    is_first_batch = True    # Is this batch first?

    while True:
      batch = download_batch(api, cfg)

      # If no more data then break.
      if len(batch) == 0:
        break
      else:
        last_candle_time = time_to_unix(batch[-1]['time'])
  
        # If the end time is limited.
        if to_time is not None:
  
          # If batch is in specified time range then append it as whole.
          if (last_candle_time <= to_time):
            result.extend(batch)
  
          # If it is larger then append only candles before `to_time`.
          else:
            for candle in batch:
              last_candle_time = time_to_unix(candle['time'])
              if last_candle_time <= to_time:
                result.append(candle)
              else:
                break
            break
  
        # If the end time is not specified, append a whole batch.
        else:
          result.extend(batch)
        
        # Move window to end of the batch.
        cfg['from_time'] = batch[-1]['time']

        # Move `from_time` to the first candle time to handle progress
        # bar properly if `from_time` is older than oldest data
        # oanda.com has.
        if is_first_batch:
          from_time = time_to_unix(batch[0]['time'])
          is_first_batch = False

        print(progress_bar(from_time, last_candle_time, to_time))
        sys.stdout.write("\033[F")
  
  return result


if __name__ == '__main__':
  parser = ArgumentParser(description="Downloads candles from oanda.com and "
    + "saves them into a csv file. Please, refer to README.md for "
    + "instructions.")

  parser.add_argument('-c', '--config', default='config.py',
    help="Path to a config.")
  config_file = parser.parse_args().config

  try:
    cfg = run_path(config_file)

    api = v20.Context(
      hostname=cfg['hostname'],
      token=cfg['token'],
      datetime_format=cfg['datetime_format']
    )
  
    # Set `from_time` to the first candle time oanda.com have if it is not
    # set.
    if cfg['from_time'] is None:
      cfg['from_time'] = MIN_TIME
  
    # Set `to_time` to current time if it is not set.
    if cfg['to_time'] is None:
      cfg['to_time'] = int(time())
  
    # Convert to UNIX time.
    cfg['from_time'] = time_to_unix(cfg['from_time'])
    cfg['to_time'] = time_to_unix(cfg['to_time'])
  
    try:
      print("Downloading candles...")
      candles = download(api, cfg)
      sys.stdout.write("\033[K")
      print("Writing to csv file...")
      write(cfg['output'], candles)
      print("Done.")
    except APIError as e:
      print(f"API error occured: {e}")
    except IOError as e:
      print(f"Can't write to an output csv file: {e}")
    except KeyError:
      print("No results.")
    except ConnectionError:
      print(f"Could not connect to {cfg['hostname']}.")
    except KeyboardInterrupt:
      print("\nKeyboard interrupt. Exiting without saving.")
  except FileNotFoundError:
    print("Can't find a config.")
