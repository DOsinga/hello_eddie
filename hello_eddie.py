#!/usr/bin/env python

import pyaudio
import audioop
import tempfile
import wave
import requests
import wit
import phue
import json
import subprocess
import pipes
import os
import platform
import feedparser
import urllib
import time
import harmony.auth
import harmony.client

WIT_TOKEN = 'QZPGUSW4QXTULXKF57XDMITD3OZYYE2M'

def say(phrase):
  if platform.system().lower() == 'darwin':
    subprocess.call(['say', str(phrase)])
  else:
    cmd = ['text2wave']
    with tempfile.NamedTemporaryFile(suffix='.wav') as out_f:
      with tempfile.SpooledTemporaryFile() as in_f:
        in_f.write(phrase)
        in_f.seek(0)
        with tempfile.SpooledTemporaryFile() as err_f:
          subprocess.call(cmd, stdin=in_f, stdout=out_f,
                          stderr=err_f)
          err_f.seek(0)
          output = err_f.read()
      subprocess.call(['afplay', out_f])


def getScore(data):
    rms = audioop.rms(data, 2)
    score = rms / 3
    return score


def transcribe(fp):
    data = fp.read()
    headers = {'Authorization': 'Bearer %s' % WIT_TOKEN,
               'accept': 'application/json',
               'Content-Type': 'audio/wav'}

    r = requests.post('https://api.wit.ai/speech?v=20150101',
                      data=data,
                      headers=headers)

    return r.json()


def listen_for_trigger(audio, PERSONA):
    """
    Listens for PERSONA in everyday sound. Times out after LISTEN_TIME, so
    needs to be restarted.
    """


    THRESHOLD_MULTIPLIER = 1.8
    RATE = 16000
    CHUNK = 1024

    # number of seconds to allow to establish threshold
    THRESHOLD_TIME = 1

    # number of seconds to listen before forcing restart
    LISTEN_TIME = 10

    # prepare recording stream
    stream = audio.open(format=pyaudio.paInt16,
                        channels=1,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
    print 'listening...'

    # stores the audio data
    frames = []

    # stores the lastN score values
    lastN = [i for i in range(30)]

    # calculate the long run average, and thereby the proper threshold
    for i in range(0, RATE / CHUNK * THRESHOLD_TIME):

        data = stream.read(CHUNK)
        frames.append(data)

        # save this data point as a score
        lastN.pop(0)
        lastN.append(getScore(data))
        average = sum(lastN) / len(lastN)

    # this will be the benchmark to cause a disturbance over!
    THRESHOLD = average * THRESHOLD_MULTIPLIER

    # save some memory for sound data
    frames = []

    # flag raised when sound disturbance detected
    didDetect = False

    # start passively listening for disturbance above threshold
    for i in range(0, RATE / CHUNK * LISTEN_TIME):

        data = stream.read(CHUNK)
        frames.append(data)
        score = getScore(data)

        if score > THRESHOLD:
            didDetect = True
            break

    # no use continuing if no flag raised
    if not didDetect:
        print "No disturbance detected"
        stream.stop_stream()
        stream.close()
        return None

    # cutoff any recording before this disturbance was detected
    frames = frames[-20:]

    # otherwise, let's keep recording for few seconds and save the file
    DELAY_MULTIPLIER = 1
    for i in range(0, RATE / CHUNK * DELAY_MULTIPLIER):

        data = stream.read(CHUNK)
        frames.append(data)

    # save the audio data
    stream.stop_stream()
    stream.close()

    with tempfile.NamedTemporaryFile(mode='w+b') as f:
        wav_fp = wave.open(f, 'wb')
        wav_fp.setnchannels(1)
        wav_fp.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
        wav_fp.setframerate(RATE)
        wav_fp.writeframes(''.join(frames))
        wav_fp.close()
        f.seek(0)

        outcomes = transcribe(f).get('outcomes', [])

        for outcome in outcomes:
          print outcome
          if outcome['intent'] == 'Eddie' and outcome['confidence'] > 0.5:
            return True
    return False


def handle_response(response):
    print('Response: {}'.format(response))


def get_entity(entities, name, value):
  values = entities.get(name)
  if not values:
    return None
  for v in values:
    if value in v:
      return v[value]
  return None


def process_query(hue, query, harmony_client, activity_map):
  outcomes = query.get('outcomes', [])
  for outcome in outcomes:
    if outcome['confidence'] < 0.5:
      say("I didn't understand: " + outcome['_text'])
    else:
      entities = outcome['entities']
      intent = outcome['intent'].lower()
      if intent == 'control_lights':
        lights_on = not get_entity(entities, 'on_off', 'value') == 'off'
        for light in hue.lights:
          if lights_on:
            light.on = True
            hue.set_light(light.light_id, 'bri', 254, transitiontime=30)
          else:
            light.on = False
      elif intent == 'get_headlines':
        say('Getting the news.')
        url = 'http://news.google.com/news?output=rss'
        topic = get_entity(entities, 'topic', 'value')
        if topic:
          url += '&q=' + urllib.quote_plus(topic)
        r = requests.get(url)
        d = feedparser.parse(r.text)
        for entry in d.entries[:5]:
          title = entry.title
          p = title.rfind('-')
          if p > -1:
            title = title[:p]
          say(title + '.')
          time.sleep(0.2)
      elif intent == 'play_band':
        band = get_entity(entities, 'wikipedia_search_query', 'value')
        if not band:
          say('I did not get that')
          continue
        else:
          say('Playing some ' + band)
          artist = requests.get('https://api.spotify.com/v1/search', params={'q': band, 'type': 'artist'}).json()
          items = artist.get('artists', {}).get('items')
          if not items:
            say("I can't find %s on spotify" % band)
            continue
          artist_id = items[0]['id']
          popular_tracks = requests.get('https://api.spotify.com/v1/artists/%s/top-tracks?country=US' % artist_id).json()
          most_popular = popular_tracks['tracks'][0]
          say('Starting with the song: ' + most_popular['name'])
      elif intent == 'harmony_one':
        device = get_entity(entities, 'device', 'value').lower()
        device_key = {'amazon tv': 'Fire TV',
                      'the beamer': 'WATCH ROKU',
                      'the projector': 'WATCH ROKU',
                      'chromecast': 'CHROMECAST',
                      'roku': 'WATCH ROKU'}.get(device)
        if not device_key:
          say("I don't know what that is")
        else:
          harmony_client.start_activity(activity_map[device_key])
          for light in hue.lights:
            if 'hallway' in light.name.lower():
              light.on = False



def main():
  audio = pyaudio.PyAudio()
  hue = phue.Bridge('172.17.172.101')

  token = harmony.auth.login('douwe.osinga@gmail.com', '1yD27amH1')
  session_token = harmony.auth.swap_auth_token('172.17.172.100', 5222, token)
  harmony_client = harmony.client.create_and_connect_client('172.17.172.100', 5222, session_token)

  config = harmony_client.get_config()
  activity_map = {act['label']: act['id'] for act in config['activity']}

  while True:
    #triggered = listen_for_trigger(audio, 'Eddie')
    triggered = True
    if triggered:
      say('What can I do for you?')
      wit.init()

      query = wit.voice_query_auto(WIT_TOKEN)

      query = json.loads(query)

      process_query(hue, query, harmony_client, activity_map)

      # Wrapping up
      wit.close()

if __name__ == '__main__':
  main()



