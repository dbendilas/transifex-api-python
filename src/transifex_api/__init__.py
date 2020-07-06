import time

import jsonapi

_api = jsonapi.JsonApi(host="https://rest.api.transifex.com")


def setup(auth, host=None):
    _api.setup(host, auth)


@_api.register
class Organization(jsonapi.Resource):
    TYPE = "organizations"


@_api.register
class Team(jsonapi.Resource):
    TYPE = "teams"


@_api.register
class Project(jsonapi.Resource):
    TYPE = "projects"


@_api.register
class Language(jsonapi.Resource):
    TYPE = "languages"


@_api.register
class Resource(jsonapi.Resource):
    TYPE = "resources"

    def purge(self):
        count = 0
        # Instead of filter, if Resource had a plural relationship to
        # ResourceString, we could do `self.fetch('resource_strings')`
        for page in list(ResourceString.filter(resource=self).all_pages()):
            count += len(page)
            ResourceString.bulk_delete(page)
        return count


@_api.register
class ResourceString(jsonapi.Resource):
    TYPE = "resource_strings"


@_api.register
class ResourceTranslation(jsonapi.Resource):
    TYPE = "resource_translations"
    EDITABLE = ["strings", 'reviewed', "proofread"]


@_api.register
class ResourceStringsAsyncUpload(jsonapi.Resource):
    TYPE = "resource_strings_async_uploads"

    @classmethod
    def upload(cls, resource, content, interval=5):
        """ Upload source content with multipart/form-data.

            :param resource: A (transifex) Resource instance or ID
            :param content: A string or file-like object
            :param interval: How often (in seconds) to poll for the completion
                             of the upload job
        """

        if isinstance(resource, Resource):
            resource = resource.id

        upload = cls.create_with_form(data={'resource': resource},
                                      files={'content': content})

        while True:
            if upload.redirect:
                return upload.follow()
            time.sleep(interval)
            upload.reload()


@_api.register
class User(jsonapi.Resource):
    TYPE = "users"
