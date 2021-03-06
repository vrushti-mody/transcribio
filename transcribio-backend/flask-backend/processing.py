import os
import ffmpeg
import hashlib
from google.cloud import speech
from multi_rake import Rake
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from firebase_admin import storage

cred = credentials.Certificate('creds/firebase.json')
firebase_admin.initialize_app(cred, {
    'storageBucket': 'transcribio-mlh.appspot.com'
})
db = firestore.client()
bucket = storage.bucket()
hash_code = ''
video_resource = ''
local_file_name = ''


def process_video(video_file_name, video_url):
    global video_resource, local_file_name
    video_resource = video_url
    local_file_name = video_file_name
    file_doc = check_existing_documents(video_file_name)
    if file_doc:
        os.remove(video_file_name)
        return file_doc
    else:
        return extract_audio(video_file_name)


def check_existing_documents(video_file_name):
    hash_md5 = hashlib.md5()
    with open(video_file_name, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    global hash_code, db
    hash_code = hash_md5.hexdigest()
    doc = db.collection(u'hash_link').document(u'{}'.format(hash_code)).get()
    if doc.exists:
        return db.collection(u'link_data').document(u'{}'.format(doc.to_dict()['perm_id'])).get().to_dict()
    else:
        return False


def extract_audio(video_file_name):
    process = (
        ffmpeg
        .input(video_file_name)
        .output('pipe:', format='s16le', acodec='pcm_s16le', ac=1, ar='16k')
        .run_async(pipe_stdout=True, pipe_stderr=True)
    )
    audio_out, err = process.communicate()

    return speech_to_text(audio_out)


def speech_to_text(raw_audio):
    client = speech.SpeechClient()
    audio = speech.RecognitionAudio(content=raw_audio)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="en-US",
        enable_word_time_offsets=True,
    )

    # Detects speech in the audio file
    response = client.recognize(request={"config": config, "audio": audio})
    transcription_results = {}
    transcription_results['transcript'] = ""
    words = {}

    for result in response.results:
        alternative = result.alternatives[0]
        transcription_results['transcript'] += alternative.transcript
        for word_info in alternative.words:
            word = word_info.word
            start_time = word_info.start_time
            if word not in words:
                words[word] = []
            words[word].append({"start_time": start_time.seconds})
        transcription_results['words'] = words

    transcription_results['keywords'] = keyword_extraction(
        transcription_results['transcript'])

    global video_resource, local_file_name
    if(video_resource is None):
        video_resource = upload_to_bucket(local_file_name)
    transcription_results['videoResource'] = video_resource
    os.remove(local_file_name)

    data = {
        "success": True,
        "result": transcription_results,
    }
    return save_data_to_firestore(data)


def save_data_to_firestore(data):
    global db, hash_code
    perm_id = hash_code[-8:]
    data['permalink'] = "https://transcribio-mlh.web.app/v/{}".format(
        perm_id)
    db.collection(u'hash_link').document(
        u'{}'.format(hash_code)).set({'perm_id': perm_id})
    db.collection(u'link_data').document(u'{}'.format(perm_id)).set(data)
    return data


def keyword_extraction(transcript):
    rake = Rake(max_words=2, min_freq=2)
    keywords = rake.apply(transcript)
    return [item[0] for item in keywords[:5]]


def get_permalink_doc(permalinkId):
    global db
    doc = db.collection(u'link_data').document(u'{}'.format(permalinkId)).get()
    if doc.exists:
        return doc.to_dict()
    else:
        raise Exception("Invalid Permalink Id")


def upload_to_bucket(video_file_name):
    global hash_code
    blob = bucket.blob('video/{}'.format(hash_code[-8:]))
    print("uploading", video_file_name)
    blob.upload_from_filename(video_file_name)
    blob.make_public()
    return blob.public_url