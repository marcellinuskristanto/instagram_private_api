import json
import time
from random import randint
import re
import warnings

from ..compat import compat_urllib_error, compat_urllib_request
from ..errors import ClientError
from ..http import MultipartFormDataEncoder
from ..utils import max_chunk_count_generator, max_chunk_size_generator, get_file_size
from ..compatpatch import ClientCompatPatch


class UploadEndpointsMixin(object):

    EXTERNAL_LOC_SOURCES = {
        'foursquare': 'foursquare_v2_id',
        'facebook_places': 'facebook_places_id',
        'facebook_events': 'facebook_events_id'
    }

    def _validate_location(self, location):
        location_keys = ['external_source', 'name', 'address']
        if type(location) != dict:
            raise ValueError('Location must be a dict.')

        # patch location object returned from location_search
        if 'external_source' not in location and 'external_id_source' in location and 'external_id' in location:
            external_source = location['external_id_source']
            location['external_source'] = external_source
            if external_source in self.EXTERNAL_LOC_SOURCES:
                location[self.EXTERNAL_LOC_SOURCES[external_source]] = location['external_id']
        for k in location_keys:
            if not location.get(k):
                raise ValueError('Location dict must contain "{0!s}".'.format(k))
        for k, val in self.EXTERNAL_LOC_SOURCES.items():
            if location['external_source'] == k and not location.get(val):
                raise ValueError('Location dict must contain "{0!s}".'.format(val))

        media_loc = {
            'name': location['name'],
            'address': location['lat'],
            'external_source': location['external_source'],
        }
        if 'lat' in location and 'lng' in location:
            media_loc['lat'] = location['lat']
            media_loc['lng'] = location['lng']
        for k, val in self.EXTERNAL_LOC_SOURCES.items():
            if location['external_source'] == k:
                media_loc['external_source'] = k
                media_loc[val] = location[val]
        return media_loc

    @classmethod
    def standard_ratios(cls):
        """
        Acceptable min, max values of with/height ratios for a standard media upload

        :return: tuple of (min. ratio, max. ratio)
        """
        # Based on IG sampling
        # and from https://help.instagram.com/1469029763400082
        return 4.0 / 5.0, 90.0 / 47.0

    @classmethod
    def reel_ratios(cls):
        """
        Acceptable min, max values of with/height ratios for a story upload

        :return: tuple of (min. ratio, max. ratio)
        """
        # min_ratio = 9.0/16.0
        # max_ratio = 3.0/4.0 if is_video else 9.0/16.0
        device_ratios = [(3, 4), (2, 3), (5, 8), (3, 5), (9, 16), (10, 16), (40, 71)]
        aspect_ratios = list(map(lambda x: 1.0 * x[0] / x[1], device_ratios))
        return min(aspect_ratios), max(aspect_ratios)

    @classmethod
    def compatible_aspect_ratio(cls, size):
        """
        Helper method to check aspect ratio for standard uploads

        :param size: tuple of (width, height)
        :return: True/False
        """
        min_ratio, max_ratio = cls.standard_ratios()
        width, height = size
        this_ratio = 1.0 * width / height
        return min_ratio <= this_ratio <= max_ratio

    @classmethod
    def reel_compatible_aspect_ratio(cls, size, is_video=False):
        """
        Helper method to check aspect ratio for story uploads

        :param size: tuple of (width, height)
        :return: True/False
        """
        warnings.warn('The is_video parameter will be removed in a future version.', FutureWarning)
        min_ratio, max_ratio = cls.reel_ratios()
        width, height = size
        this_ratio = 1.0 * width / height
        return min_ratio <= this_ratio <= max_ratio

    def configure(self, upload_id, size, caption='', location=None,
                  disable_comments=False, is_sidecar=False):
        """
        Finalises a photo upload. This should not be called directly.
        Use :meth:`post_photo` instead.

        :param upload_id:
        :param size: tuple of (width, height)
        :param caption:
        :param location: a dict of venue/location information,
                         from :meth:`location_search` or :meth:`location_fb_search`
        :param disable_comments:
        :param is_sidecar: bool flag for album upload
        :return:
        """
        if not self.compatible_aspect_ratio(size):
            raise ValueError('Incompatible aspect ratio.')

        endpoint = 'media/configure/'
        width, height = size
        params = {
            'caption': caption,
            'media_folder': 'Instagram',
            'source_type': '4',
            'upload_id': upload_id,
            'device': {
                'manufacturer': self.phone_manufacturer,
                'model': self.phone_device,
                'android_version': self.android_version,
                'android_release': self.android_release
            },
            'edits': {
                'crop_original_size': [width * 1.0, height * 1.0],
                'crop_center': [0.0, -0.0],
                'crop_zoom': 1.0
            },
            'extra': {
                'source_width': width,
                'source_height': height,
            }
        }
        if location:
            media_loc = self._validate_location(location)
            params['location'] = json.dumps(media_loc)
            if 'lat' in location and 'lng' in location:
                params['geotag_enabled'] = '1'
                params['exif_latitude'] = '0.0'
                params['exif_longitude'] = '0.0'
                params['posting_latitude'] = str(location['lat'])
                params['posting_longitude'] = str(location['lng'])
                params['media_latitude'] = str(location['lat'])
                params['media_latitude'] = str(location['lng'])
        if disable_comments:
            params['disable_comments'] = '1'

        if is_sidecar:
            return params

        params.update(self.authenticated_params)
        res = self._call_api(endpoint, params=params)
        if self.auto_patch and res.get('media'):
            ClientCompatPatch.media(res.get('media'), drop_incompat_keys=self.drop_incompat_keys)
        return res

    def configure_video(self, upload_id, size, duration, thumbnail_data, caption='',
                        location=None, disable_comments=False, is_sidecar=False):
        """
        Finalises a video upload. This should not be called directly.
        Use :meth:`post_video` instead.

        :param upload_id:
        :param size: tuple of (width, height)
        :param duration: in seconds
        :param thumbnail_data: byte string of thumbnail photo
        :param caption:
        :param location: a dict of venue/location information,
                         from :meth:`location_search` or :meth:`location_fb_search`
        :param disable_comments:
        :param is_sidecar: bool flag for album upload
        :return:
        """
        if not self.compatible_aspect_ratio(size):
            raise ValueError('Incompatible aspect ratio.')

        # upload video thumbnail
        self.post_photo(thumbnail_data, size, caption, upload_id, location=location,
                        disable_comments=disable_comments, is_sidecar=is_sidecar)

        width, height = size
        params = {
            'upload_id': upload_id,
            'caption': caption,
            'source_type': '3',
            'poster_frame_index': 0,
            'length': duration * 1.0,
            'audio_muted': False,
            'filter_type': '0',
            'video_result': 'deprecated',
            'clips': {
                'length': duration * 1.0,
                'source_type': '3',
                'camera_position': 'back'
            },
            'device': {
                'manufacturer': self.phone_manufacturer,
                'model': self.phone_device,
                'android_version': self.android_version,
                'android_release': self.android_release
            },
            'extra': {
                'source_width': width,
                'source_height': height
            }
        }
        if disable_comments:
            params['disable_comments'] = '1'
        if location:
            media_loc = self._validate_location(location)
            params['location'] = json.dumps(media_loc)
            if 'lat' in location and 'lng' in location:
                params['geotag_enabled'] = '1'
                params['av_latitude'] = '0.0'
                params['av_longitude'] = '0.0'
                params['posting_latitude'] = str(location['lat'])
                params['posting_longitude'] = str(location['lng'])
                params['media_latitude'] = str(location['lat'])
                params['media_latitude'] = str(location['lng'])

        if is_sidecar:
            return params

        params.update(self.authenticated_params)
        res = self._call_api('media/configure/', params=params, query={'video': 1})
        if res.get('media') and self.auto_patch:
            ClientCompatPatch.media(res.get('media'), drop_incompat_keys=self.drop_incompat_keys)
        return res

    def configure_to_reel(self, upload_id, size):
        """
        Finalises a photo story upload. This should not be called directly.
        Use :meth:`post_photo_story` instead.

        :param upload_id:
        :param size: tuple of (width, height)
        :return:
        """
        if not self.reel_compatible_aspect_ratio(size):
            raise ValueError('Incompatible aspect ratio.')

        endpoint = 'media/configure_to_story/'
        width, height = size
        params = {
            'source_type': '4',
            'upload_id': upload_id,
            'story_media_creation_date': str(int(time.time()) - randint(11, 20)),
            'client_shared_at': str(int(time.time()) - randint(3, 10)),
            'client_timestamp': str(int(time.time())),
            'configure_mode': 1,      # 1 - REEL_SHARE, 2 - DIRECT_STORY_SHARE
            'device': {
                'manufacturer': self.phone_manufacturer,
                'model': self.phone_device,
                'android_version': self.android_version,
                'android_release': self.android_release
            },
            'edits': {
                'crop_original_size': [width * 1.0, height * 1.0],
                'crop_center': [0.0, 0.0],
                'crop_zoom': 1.3333334
            },
            'extra': {
                'source_width': width,
                'source_height': height,
            }
        }
        params.update(self.authenticated_params)
        res = self._call_api(endpoint, params=params)
        if self.auto_patch and res.get('media'):
            ClientCompatPatch.media(res.get('media'), drop_incompat_keys=self.drop_incompat_keys)
        return res

    def configure_video_to_reel(self, upload_id, size, duration, thumbnail_data):
        """
        Finalises a video story upload. This should not be called directly.
        Use :meth:`post_video_story` instead.

        :param upload_id:
        :param size: tuple of (width, height)
        :param duration: in seconds
        :param thumbnail_data: byte string of thumbnail photo
        :return:
        """
        if not self.reel_compatible_aspect_ratio(size):
            raise ValueError('Incompatible aspect ratio.')

        res = self.post_photo(thumbnail_data, size, '', upload_id=upload_id, to_reel=True)

        width, height = size
        params = {
            'source_type': '4',
            'upload_id': upload_id,
            'story_media_creation_date': str(int(time.time()) - randint(11, 20)),
            'client_shared_at': str(int(time.time()) - randint(3, 10)),
            'client_timestamp': str(int(time.time())),
            'configure_mode': 1,      # 1 - REEL_SHARE, 2 - DIRECT_STORY_SHARE
            'poster_frame_index': 0,
            'length': duration * 1.0,
            'audio_muted': False,
            'filter_type': '0',
            'video_result': 'deprecated',
            'clips': {
                'length': duration * 1.0,
                'source_type': '4',
                'camera_position': 'back'
            },
            'device': {
                'manufacturer': self.phone_manufacturer,
                'model': self.phone_device,
                'android_version': self.android_version,
                'android_release': self.android_release
            },
            'extra': {
                'source_width': width,
                'source_height': height,
            },
        }

        params.update(self.authenticated_params)
        res = self._call_api('media/configure_to_story/', params=params, query={'video': '1'})
        if self.auto_patch and res.get('media'):
            ClientCompatPatch.media(res.get('media'), drop_incompat_keys=self.drop_incompat_keys)
        return res

    def post_photo(self, photo_data, size, caption='', upload_id=None, to_reel=False, **kwargs):
        """
        Upload a photo.

        [CAUTION] FLAKY, IG is very finicky about sizes, etc, needs testing.

        :param photo_data: byte string of the image
        :param size: tuple of (width, height)
        :param caption:
        :param upload_id:
        :param to_reel: a Story photo
        :param kwargs:
            - **location**: a dict of venue/location information, from :meth:`location_search`
              or :meth:`location_fb_search`
            - **disable_comments**: bool to disable comments
        :return:
        """
        warnings.warn('This endpoint has not been fully tested.', UserWarning)

        # if upload_id is provided, it's a thumbnail for a vid upload
        for_video = True if upload_id else False

        if not for_video:
            if not to_reel and not self.compatible_aspect_ratio(size):
                raise ValueError('Incompatible aspect ratio.')
            if to_reel and not self.reel_compatible_aspect_ratio(size):
                raise ValueError('Incompatible reel aspect ratio.')
            if not 320 <= size[0] <= 1080:
                # range from https://help.instagram.com/1631821640426723
                raise ValueError('Invalid image width.')

        location = kwargs.pop('location', None)
        if location:
            self._validate_location(location)
        disable_comments = True if kwargs.pop('disable_comments', False) else False

        is_sidecar = kwargs.pop('is_sidecar', False)
        if not upload_id:
            upload_id = str(int(time.time() * 1000))

        endpoint = 'upload/photo/'
        fields = [
            ('upload_id', upload_id),
            ('_uuid', self.uuid),
            ('_csrftoken', self.csrftoken),
            ('image_compression', '{"lib_name":"jt","lib_version":"1.3.0","quality":"87"}')
        ]
        if is_sidecar:
            fields.append(('is_sidecar', '1'))
            if for_video:
                fields.append(('media_type', '2'))

        files = [
            ('photo', 'pending_media_{0!s}{1!s}'.format(str(int(time.time() * 1000)), '.jpg'),
             'application/octet-stream', photo_data)
        ]

        content_type, body = MultipartFormDataEncoder(self.uuid).encode(fields, files)
        headers = self.default_headers
        headers['Content-Type'] = content_type
        headers['Content-Length'] = len(body)

        req = compat_urllib_request.Request(self.api_url + endpoint, body, headers=headers)
        try:
            self.logger.debug('POST {0!s}'.format(self.api_url) + endpoint)
            response = self.opener.open(req, timeout=self.timeout)
        except compat_urllib_error.HTTPError as e:
            error_msg = e.reason
            error_response = self._read_response(e)
            self.logger.debug('RESPONSE: {0:d} {1!s}'.format(e.code, error_response))
            try:
                error_obj = json.loads(error_response)
                if error_obj.get('message'):
                    error_msg = '{0!s}: {1!s}'.format(e.reason, error_obj['message'])
            except:
                # do nothing, prob can't parse json
                pass
            raise ClientError(error_msg, e.code, error_response)

        post_response = self._read_response(response)
        self.logger.debug('RESPONSE: {0:d} {1!s}'.format(response.code, post_response))
        json_response = json.loads(post_response)

        if for_video and is_sidecar:
            return json_response

        upload_id = json_response['upload_id']

        # # NOTES: Logging traffic doesn't seem to indicate any additional "configure" after upload
        # # BUT not doing a "configure" causes a video post to fail with a
        # # "Other media configure error: b'yEmZkUpAj4'" error
        # if for_video:
        #     logger.debug('Skip photo configure.')
        #     return json_response
        if to_reel:
            return self.configure_to_reel(upload_id, size)
        else:
            return self.configure(upload_id, size, caption=caption, location=location,
                                  disable_comments=disable_comments, is_sidecar=is_sidecar)

    def post_video(self, video_data, size, duration, thumbnail_data, caption='', to_reel=False, **kwargs):
        """
        Upload a video

        [CAUTION] FLAKY, IG is very picky about sizes, etc, needs testing.

        :param video_data: byte string or a file-like object of the video content
        :param size: tuple of (width, height)
        :param duration: in seconds
        :param thumbnail_data: byte string of the video thumbnail content
        :param caption:
        :param to_reel: post to reel as Story
        :param kwargs:
             - **location**: a dict of venue/location information, from :meth:`location_search`
               or :meth:`location_fb_search`
             - **disable_comments**: bool to disable comments
             - **max_retry_count**: maximum attempts to reupload. Default 10.
        :return:
        """
        warnings.warn('This endpoint has not been fully tested.', UserWarning)

        if not to_reel and not self.compatible_aspect_ratio(size):
            raise ValueError('Incompatible aspect ratio.')

        if to_reel and not self.reel_compatible_aspect_ratio(size):
            raise ValueError('Incompatible reel aspect ratio.')

        if not 612 <= size[0] <= 1080:
            # range was determined through sampling of video uploads
            raise ValueError('Invalid video width.')

        if duration < 3.0:
            raise ValueError('Duration is less than 3s.')

        if not to_reel and duration > 60.0:
            raise ValueError('Duration is more than 60s.')

        if to_reel and duration > 15.0:
            raise ValueError('Duration is more than 15s.')

        max_file_len = 50 * 1024 * 1000
        try:
            video_file_len = len(video_data)
        except TypeError:
            video_file_len = get_file_size(video_data)
        if video_file_len > max_file_len:
            raise ValueError('Video file is too big.')

        location = kwargs.pop('location', None)
        if location:
            self._validate_location(location)
        disable_comments = True if kwargs.pop('disable_comments', False) else False

        endpoint = 'upload/video/'
        upload_id = str(int(time.time() * 1000))

        width, height = size
        params = {
            '_csrftoken': self.csrftoken,
            '_uuid': self.uuid,
            'upload_id': upload_id,
        }
        is_sidecar = kwargs.pop('is_sidecar', False)
        if is_sidecar:
            params['is_sidecar'] = '1'
        else:
            params.update({
                'media_type': '2',
                'upload_media_duration_ms': int(duration * 1000),
                'upload_media_width': width,
                'upload_media_height': height
            })

        res = self._call_api(endpoint, params=params, unsigned=True)
        upload_url = res['video_upload_urls'][-1]['url']
        upload_job = res['video_upload_urls'][-1]['job']

        # Prevent excessively small chunks
        if video_file_len > 1 * 1024 * 1000:
            # max num of chunks = 4
            chunk_generator = max_chunk_count_generator(4, video_data)
        else:
            # max chunk size = 350,000 so that we'll always have
            # <4 chunks when it's <1mb
            chunk_generator = max_chunk_size_generator(350000, video_data)

        successful_chunk_ranges = []
        all_done = False

        max_retry_count = kwargs.pop('max_retry_count', 10)
        configure_delay = 0
        for _ in range(max_retry_count + 1):
            for chunk, data in chunk_generator:
                skip_chunk = False
                for received_chunk in successful_chunk_ranges:
                    if received_chunk[0] <= chunk.start and received_chunk[1] >= (chunk.end - 1):
                        skip_chunk = True
                        break
                if skip_chunk:
                    self.logger.debug('Skipped chunk: {0:d} - {1:d}'.format(chunk.start, chunk.end - 1))
                    continue

                # data = video_data[chunk.start: chunk.end]
                headers = self.default_headers
                headers['Connection'] = 'keep-alive'
                headers['Content-Type'] = 'application/octet-stream'
                headers['Content-Disposition'] = 'attachment; filename="video.mov"'
                headers['Session-ID'] = upload_id
                if is_sidecar:
                    headers['Cookie'] = 'sessionid=' + self.get_cookie_value('sessionid')
                headers['job'] = upload_job
                headers['Content-Length'] = chunk.length
                headers['Content-Range'] = 'bytes {0:d}-{1:d}/{2:d}'.format(chunk.start, chunk.end - 1, video_file_len)
                self.logger.debug('POST {0!s}'.format(upload_url))
                self.logger.debug('Uploading Content-Range: {0!s}'.format(headers['Content-Range']))

                req = compat_urllib_request.Request(
                    str(upload_url), data=data, headers=headers)

                try:
                    res = self.opener.open(req, timeout=self.timeout)
                    post_response = self._read_response(res)
                    self.logger.debug('RESPONSE: {0:d} {1!s}'.format(res.code, post_response))
                    if res.info().get('Content-Type') == 'application/json':
                        # last chunk
                        upload_res = json.loads(post_response)
                        configure_delay = int(upload_res.get('configure_delay_ms', 0)) / 1000.0
                        all_done = True
                        break
                    else:
                        successful_chunk_ranges = []
                        post_progress = post_response.split(',')
                        for progress in post_progress:
                            mobj = re.match(r'(?P<start>[0-9]+)\-(?P<end>[0-9]+)/(?P<total>[0-9]+)', progress)
                            if mobj:
                                successful_chunk_ranges.append((int(mobj.group('start')), int(mobj.group('end'))))
                            else:
                                self.logger.error('Received unexpected chunk upload response: {0!s}'.format(post_response))
                                raise ClientError(
                                    'Upload has failed due to unexpected upload response: {0!s}'.format(post_response),
                                    code=500)

                except compat_urllib_error.HTTPError as e:
                    error_msg = e.reason
                    error_response = self._read_response(e)
                    self.logger.debug('RESPONSE: {0:d} {1!s}'.format(e.code, error_response))
                    try:
                        error_obj = json.loads(error_response)
                        if error_obj.get('message'):
                            error_msg = '{0!s}: {1!s}'.format(e.reason, error_obj['message'])
                    except:
                        # do nothing, prob can't parse json
                        pass
                    raise ClientError(error_msg, e.code, error_response)
            else:
                # if not break due to completed chunks then continue with next chunk
                continue
            # a break occurred, therefore skip any remaining retry attempts
            break

        if not all_done:
            raise ClientError('Upload has failed due to incomplete chunk uploads.', code=500)

        if not configure_delay:
            configure_delay = 3
        configure_retry_max = 2

        for i in range(1, configure_retry_max + 1):
            try:
                if not to_reel:
                    result = self.configure_video(
                        upload_id, size, duration, thumbnail_data, caption=caption, location=location,
                        disable_comments=disable_comments, is_sidecar=is_sidecar)
                else:
                    result = self.configure_video_to_reel(
                        upload_id, size, duration, thumbnail_data)
                return result
            except ClientError as ce:
                if (ce.code == 202 or ce.msg == 'Transcode timeout') and i < configure_retry_max:
                    self.logger.warn('Retry configure after {0:f} seconds'.format(configure_delay))
                    time.sleep(configure_delay)
                else:
                    raise

    def post_photo_story(self, photo_data, size):
        """
        Upload a photo story

        :param photo_data: byte string of the image
        :param size: tuple of (width, height)
        :return:
        """
        return self.post_photo(
            photo_data=photo_data, size=size, to_reel=True)

    def post_video_story(self, video_data, size, duration, thumbnail_data):
        """
        Upload a video story

        :param video_data: byte string or a file-like object of the video content
        :param size: tuple of (width, height)
        :param duration: in seconds
        :param thumbnail_data: byte string of the video thumbnail content
        :return:
        """
        return self.post_video(
            video_data=video_data, size=size, duration=duration,
            thumbnail_data=thumbnail_data, to_reel=True)

    def post_album(self, medias, caption='', location=None, **kwargs):
        """
        Post an album of up to 10 photos/videos.

        :param medias: an iterable list/collection of media dict objects

            .. code-block:: javascript

                medias = [
                    {"type": "image", "size": (720, 720), "data": "..."},
                    {
                        "type": "image", "size": (720, 720),
                        "usertags": [{"user_id":4292127751, "position":[0.625347,0.4384531]}],
                        "data": "..."
                    },
                    {"type": "video", "size": (720, 720), "duration": 12.4, "thumbnail": "...", "data": "..."}
                ]

        :param caption:
        :param location:
        :return:
        """
        album_upload_id = str(int(time.time() * 1000))
        children_metadata = []
        for media in medias:
            if len(children_metadata) >= 10:
                continue
            if media.get('type', '') not in ['image', 'video']:
                raise ValueError('Invalid media type: {0!s}'.format(media.get('type', '')))
            if not media.get('data'):
                raise ValueError('Data not specified.')
            if not media.get('size'):
                raise ValueError('Size not specified.')
            if media['type'] == 'video':
                if not media.get('duration'):
                    raise ValueError('Duration not specified.')
                if not media.get('thumbnail'):
                    raise ValueError('Thumbnail not specified.')
            aspect_ratio = (media['size'][0] * 1.0) / (media['size'][1] * 1.0)
            if aspect_ratio > 1.0 or aspect_ratio < 1.0:
                raise ValueError('Invalid media aspect ratio.')

            if media['type'] == 'video':
                metadata = self.post_video(
                    video_data=media['data'],
                    size=media['size'],
                    duration=media['duration'],
                    thumbnail_data=media['thumbnail'],
                    is_sidecar=True
                )
            else:
                metadata = self.post_photo(
                    photo_data=media['data'],
                    size=media['size'],
                    is_sidecar=True,
                )
                if media.get('usertags'):
                    usertags = media['usertags']
                    utags = {'in': [{'user_id': str(u['user_id']), 'position': u['position']} for u in usertags]}
                    metadata['usertags'] = json.dumps(utags, separators=(',', ':'))
            children_metadata.append(metadata)

        if len(children_metadata) <= 1:
            raise ValueError('Invalid number of media objects: {0:d}'.format(len(children_metadata)))

        # configure as sidecar
        endpoint = 'media/configure_sidecar/'
        params = {
            'caption': caption,
            'client_sidecar_id': album_upload_id,
            'children_metadata': children_metadata
        }
        if location:
            media_loc = self._validate_location(location)
            params['location'] = json.dumps(media_loc)
            if 'lat' in location and 'lng' in location:
                params['geotag_enabled'] = '1'
                params['exif_latitude'] = '0.0'
                params['exif_longitude'] = '0.0'
                params['posting_latitude'] = str(location['lat'])
                params['posting_longitude'] = str(location['lng'])
                params['media_latitude'] = str(location['lat'])
                params['media_latitude'] = str(location['lng'])
        disable_comments = kwargs.pop('disable_comments', False)
        if disable_comments:
            params['disable_comments'] = '1'

        params.update(self.authenticated_params)
        res = self._call_api(endpoint, params=params)
        if self.auto_patch and res.get('media'):
            ClientCompatPatch.media(res.get('media'), drop_incompat_keys=self.drop_incompat_keys)
        return res
