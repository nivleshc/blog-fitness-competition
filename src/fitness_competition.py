from logging import exception
from boto3.s3.transfer import TransferConfig
from datetime import datetime, timedelta
from pytz import timezone
import requests
import math
import os
import boto3
import botocore
import json

### --- start of constants and variables declaration --- ###
slack_webhook_url = os.environ["SLACK_WEBHOOK_URL"]
artefacts_s3_bucket = os.environ["ARTEFACTS_S3_BUCKET"]
artefacts_s3_key_prefix = os.environ["ARTEFACTS_S3_KEY_PREFIX"]

fitbit_challenge_statistics_filename = artefacts_s3_key_prefix + "/fitbit_competition_stats.json"
s3_transfer_config = TransferConfig(use_threads=False)

fitbit_daily_steps_goal = 10000 # daily goal to attain, to get 1 point
fitbit_daily_bonus_points_steps = 1000 # for every this many steps obtained over the daily goal, allocate 1 extra point

my_timezone = 'Australia/Sydney'  # timezone

competition_statistics = None

### --- end of constants and variables declaration --- ###


# this class will take care of the fitness competition statistics
class CompetitionStatistics:

  fitbit_activity_url = 'https://api.fitbit.com/1/user/'
  fitbit_refresh_token_url = 'https://api.fitbit.com/oauth2/token'

  yesterday = datetime.now(timezone(my_timezone)) - timedelta(days=1)
  yesterday_str = yesterday.strftime('%Y-%m-%d')

  # initialise things when this class object is created
  def __init__(self, fitbit_daily_steps_goal, fitbit_daily_bonus_points_steps):
    
    self.fitbit_daily_steps_goal = fitbit_daily_steps_goal
    self.fitbit_daily_bonus_points_steps = fitbit_daily_bonus_points_steps

    self.statistics = {}
    self.statistics['start-date'] = self.yesterday_str
    self.statistics['competition-day'] = '1' # number of days this compettion has been running, first day is competition-day=1

    players = []
    self.statistics['players'] = players

  # this function imports previous statistics from an object.
  def import_previous_stats(self, previous_challenge_statistics):
    self.statistics['start-date'] = previous_challenge_statistics['start-date']
    self.statistics['competition-day'] = int(previous_challenge_statistics['competition-day']) + 1
    self.statistics['players'] = previous_challenge_statistics['players']

  # given a player's name, this function finds the location in the players dict, where this particular players statistics are located.
  # if found, it returns the location otherwise returns -1
  def find_player_record_location(self, player_name):
    record_location = -1
    pointer = 0

    while (record_location == -1) and (pointer < len(self.statistics['players'])):
      if self.statistics['players'][pointer]['name'] == player_name:
        record_location = pointer
      else:
        pointer += 1
    
    return record_location
  
  # given total-points, this function finds the location in the players dict, of all players who have this total-points
  # if found, it returns the locations as a comma separated string, otherwise returns ''
  def find_player_record_location_with_total_points(self, player_total_points):
    record_location = ''
    pointer = 0

    while (pointer < len(self.statistics['players'])):
      if self.statistics['players'][pointer]['total-points'] == player_total_points:
        record_location += str(pointer) + ','
      
      pointer += 1

    # remove the trailing ,
    record_location = record_location[0:-1]

    return record_location

  # this function retrieves the fitbit steps for the specified user. returns -1 if there was an error  
  def get_fitbit_steps(self, fitbit_access_token, user_name, user_id, activity_date):
    header = {
        'Authorization': 'Bearer ' + fitbit_access_token
    }

    url = self.fitbit_activity_url + user_id + '/activities/date/' + activity_date + '.json'
    try:
      response = requests.get(url, headers=header).json()
      steps = response['summary']['steps']
      print('>>get_fitbit_steps:user=' + user_name + ' date=' + activity_date + ' steps=' + str(response['summary']['steps']))

    except Exception as e:
      print('>>get_fitbit_steps:Error:user=' + user_id + ' date=' + activity_date + ' url=' + url + ' error=' + str(e))
      steps = -1

    return steps

  # this function refreshs a fitbit token
  def refresh_fitbit_token(self, base64_client_id_client_secret, refresh_token):
    myheader = {
        'Authorization': 'Basic ' + base64_client_id_client_secret,
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    mydata = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token
    }

    response = requests.post(self.fitbit_refresh_token_url, headers=myheader, data=mydata).json()

    return response

  # this function calculates a player's competition points
  def calculate_competition_points(self, player):
    ssm_client = boto3.client('ssm')

    # retrieve the player's token and secret from AWS SSM Parameter Store
    player_token = json.loads(ssm_client.get_parameter(Name='fitbit_token_' + player, WithDecryption=True)['Parameter']['Value'])
    player_secret = ssm_client.get_parameter(Name='fitbit_secret_' + player,WithDecryption=True)['Parameter']['Value']

    # since this lambda will be run every 24 hours, assume that the tokens has expired (max age is 8hours). Refresh the token
    player_token = self.refresh_fitbit_token(player_secret, player_token['refresh_token'])
    
    token_is_valid = False

    # check that a valid token was obtained after refreshing. Do not continue if there was an error.
    try:
      if (player_token['access_token']):
        token_is_valid = True

    except Exception as e:
      print('>>calculate_competition_points:error refreshing token [player=' + player + ']ErrorType:' + player_token['errors'][0]['errorType'] + ' Error:' + player_token['errors'][0]['message'])

    if (token_is_valid):
      # retrieve the player's steps for yesterday
      player_steps = self.get_fitbit_steps(player_token['access_token'], player, player_token['user_id'], self.yesterday_str)

      points = 0

      # if the player achieved the daily steps goal, award 1 point
      if (player_steps >= self.fitbit_daily_steps_goal):
        points = 1

        # give bonus points for additional steps above the daily steps goal (additional steps must be in daily_bonus_points_steps increments)
        additional_steps = player_steps - self.fitbit_daily_steps_goal
        bonus_points = math.floor(additional_steps / self.fitbit_daily_bonus_points_steps)
        points += bonus_points

      # update the players statistics
      record_location = self.find_player_record_location(player)

      if (record_location != -1):
        self.statistics['players'][record_location]['yesterdays-steps'] = player_steps
        self.statistics['players'][record_location]['yesterdays-points'] = points
        self.statistics['players'][record_location]['total-steps']  += player_steps
        self.statistics['players'][record_location]['total-points'] += points
        print('>>calculate_competition_points:points updated for player=' + player)
      else:
        # new player found. create a new record for this player in statistics
        print('>>calculate_competition_points: new player found: [' + player + ']')

        player_record = {}
        player_record['name'] = player
        player_record['yesterdays-steps'] = player_steps
        player_record['yesterdays-points'] = points
        player_record['total-steps'] = player_steps        
        player_record['total-points'] = points
        player_record['rank'] = ''  # this will be updated after all players points has been calculated

        self.statistics['players'].append(player_record)

      # update the AWS SSM Parameter Store parameter with the refreshed token
      update_player_token_response = ssm_client.put_parameter(Name='fitbit_token_' + player, Value=json.dumps(player_token), Overwrite=True)
      print('>>calculate_competition_points:updating AWS SSM Parameter Store with latest token for player='+ player + ' result:' + str(update_player_token_response))

      status = 'success'
    else:
      # refresh token failed for this player
      status = 'error:' + player_token['errors'][0]['message']
      
    return status
  
  # this function calculates each player's rank based on their total-points. The records in self.statistics['players'] dict is also rearranged
  # so that the player with rank=1 is at location zero, rank=2 is at location 1 etc.
  def calculate_players_rank(self):
    
    rank = []  # make a list that contains all the current total-points in the competition. this will then be sorted to get the rank
    
    for record in self.statistics['players']:
      rank.append(record['total-points'])
      
    # remove duplicate total-points from rank
    rank = list(dict.fromkeys(rank))

    # sort rank list using total-points from highest -> lowest
    rank.sort(reverse=True)

    ranked_players_record = [] # this dict will contain player records sorted according to their rank

    # go through the sorted total-points, find the player for whom the points belong and give them that rank.
    for position in range(0, len(rank)):
      # find players which have total-points equal to the current ranks total-points
      player_record_locations = self.find_player_record_location_with_total_points(rank[position])

      if (player_record_locations != ''): # cater for scenarios where there could be more than one player with the same total-points
        for location in player_record_locations.split(','):
          self.statistics['players'][int(location)]['rank'] = position + 1

          # add this player's record to the new dict that will contain player records based on their rank
          ranked_players_record.append(self.statistics['players'][int(location)])
      else:
        print('>>calculate_player_rank:error:player with [total-points=' + rank[position-1] + '] not found.')
    
    # replace the players record with the ranked players record
    self.statistics['players'] = ranked_players_record
    print('>>calculate_players_rank:ranks:'+str(self.statistics['players']))

  # this function sends a slack notification with the competition statistics
  def send_competition_results_notification(self, slack_webhook_url):

    # create a message based on the players statistics
    competition_day = (datetime.strptime(self.yesterday_str, '%Y-%m-%d') - datetime.strptime(self.statistics['start-date'], '%Y-%m-%d')).days + 1
    slack_message = 'Competition Day=' + str(competition_day) + '[' + self.yesterday_str + '][Competition Start-date=' + str(self.statistics['start-date']) + ']'

    for records in self.statistics['players']:
      slack_message += '\nRank=' + str(records['rank']) + ' ' + str(records['name']).capitalize() + ' | Steps[yesterday]=' + str(records['yesterdays-steps'])
      
      if records['yesterdays-points'] >= 1:
        slack_message += ' | Congrats! You earned ' + str(records['yesterdays-points']) + ' point(s)'
      else:
        slack_message += ' | No points earned :( Try harder today'
      
      slack_message += ' | Steps[total]=' + str(records['total-steps']) 
      slack_message += ' | Points[total]=' + str(records['total-points'])

    slack_payload = {"text": slack_message}

    response = requests.post(slack_webhook_url, json.dumps(slack_payload))
    response_json = response.text
    print('>>send_slack_message:response after posting to slack:' + str(response_json))
  
  # this function writes the contents of self.statistics to file at filepath
  def write_competition_statistics_to_file(self, filepath):
    with open(filepath, 'w') as output_file:
        json.dump(self.statistics, output_file)
    

def run_fitness_competition():
  s3 = boto3.resource('s3')
  ssm_client = boto3.client('ssm')

  # check if statistics from previous run exists.
  try:
      s3.Object(artefacts_s3_bucket, fitbit_challenge_statistics_filename).load()
      print('>>run_fitbit_challenge:statistics from previous run found. Importing...')
      previous_challenge_statistics = json.loads(s3.Object(artefacts_s3_bucket, fitbit_challenge_statistics_filename).get()['Body'].read().decode('utf-8'))
      competition_statistics.import_previous_stats(previous_challenge_statistics)
      
  except botocore.exceptions.ClientError as e:
      if e.response['Error']['Code'] == "404":
          # fitbit challenge statistics file does not exist. We will treat this run as the start of the competition.
          print('>>run_fitbit_challenge:No statistics from previous run found. Treating this run as the start of the competition.')
         
  # get the names of all fitbit challenge players
  fitbit_challenge_players = ssm_client.get_parameter(Name='fitbit_challenge_players')['Parameter']['Value']
  print('>>run_fitbit_challenge:players found:'+str(fitbit_challenge_players))

  status = ''
  # calculate each player's points based on yesterday's steps
  for player in fitbit_challenge_players.split(','):
    calculate_points_status = competition_statistics.calculate_competition_points(player)
    if (calculate_points_status != 'success'):
      print('>>run_fitbit_challenge:players:error calculating competition points for ' + player + ' ' + calculate_points_status)
      status += calculate_points_status + ' '
  
  status = status.strip()

  # rank the players based on their points
  competition_statistics.calculate_players_rank()

  # send a slack message with the latest steps, points and rank
  competition_statistics.send_competition_results_notification(slack_webhook_url)

  # write the competition statistics to a local file, which will be uploaded to s3. This file will be used in next lambda run
  local_fitbit_challenge_statistics_filename = '/tmp' + fitbit_challenge_statistics_filename.replace(artefacts_s3_key_prefix,'')
  competition_statistics.write_competition_statistics_to_file(local_fitbit_challenge_statistics_filename)

  # upload the local fitbit competition statistics file to artefacts s3 bucket
  s3.Bucket(artefacts_s3_bucket).upload_file(local_fitbit_challenge_statistics_filename,fitbit_challenge_statistics_filename, Config=s3_transfer_config)
  print('>>run_fitbit_challenge:uploaded updated fitbit competition statistics file to artefacts S3 bucket: ' +
        local_fitbit_challenge_statistics_filename + ' -> [' + artefacts_s3_bucket + ']' + fitbit_challenge_statistics_filename)

  if (status == ''):
    status = 'success'

  return status
  

def lambda_handler(event, context):
  global competition_statistics
  
  competition_statistics = CompetitionStatistics(fitbit_daily_steps_goal, fitbit_daily_bonus_points_steps)
  
  status = run_fitness_competition()

  # cleanup  
  del competition_statistics  # delete this object as it contains sensitive information

  return {
      'statusCode': 200,
      'body': json.dumps(status)
  }
