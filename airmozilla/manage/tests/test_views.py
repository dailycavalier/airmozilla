import datetime
import json
import random
from cStringIO import StringIO

from django.conf import settings
from django.contrib.auth.models import User, Group, Permission
from django.contrib.flatpages.models import FlatPage
from django.test import TestCase
from django.core import mail
from django.utils.timezone import utc

from funfactory.urlresolvers import reverse

from nose.tools import eq_, ok_
from mock import patch

from airmozilla.main.models import (Approval, Category, Event, EventOldSlug,
                                    Location, Participant, Template)


class ManageTestCase(TestCase):
    fixtures = ['airmozilla/manage/tests/main_testdata.json']

    def setUp(self):
        self.user = User.objects.create_superuser('fake', 'fake@f.com', 'fake')
        assert self.client.login(username='fake', password='fake')

    def _delete_test(self, obj, remove_view, redirect_view):
        """Common test for deleting an object in the management interface,
           checking that it was deleted properly, and ensuring that an improper
           delete request does not remove the object."""
        model = obj.__class__
        url = reverse(remove_view, kwargs={'id': obj.id})
        self.client.get(url)
        obj = model.objects.get(id=obj.id)
        ok_(obj)  # the template wasn't deleted because we didn't use POST
        response_ok = self.client.post(url)
        self.assertRedirects(response_ok, reverse(redirect_view))
        obj = model.objects.filter(id=obj.id).exists()
        ok_(not obj)


class TestPermissions(ManageTestCase):
    def test_unauthorized(self):
        """ Client with no log in - should be rejected. """
        self.client.logout()
        response = self.client.get(reverse('manage:home'))
        self.assertRedirects(response, settings.LOGIN_URL
                             + '?next=' + reverse('manage:home'))

    def test_not_staff(self):
        """ User is not staff - should be rejected. """
        self.user.is_staff = False
        self.user.save()
        response = self.client.get(reverse('manage:home'))
        self.assertRedirects(response, settings.LOGIN_URL
                             + '?next=' + reverse('manage:home'))

    def test_staff_home(self):
        """ User is staff - should get an OK homepage. """
        response = self.client.get(reverse('manage:home'))
        eq_(response.status_code, 200)

    def test_staff_logout(self):
        """ Log out makes admin inaccessible. """
        self.client.get(reverse('auth:logout'))
        response = self.client.get(reverse('manage:home'))
        self.assertRedirects(response, settings.LOGIN_URL
                             + '?next=' + reverse('manage:home'))


class TestDashboard(ManageTestCase):

    # XXX Using `override_settings` doesn't work because of a bug in `tower`.
    # Once that's fixed start using `override_settings` in the tests instead.
    #@override_settings(ADMINS=(('Bob', 'bob@example.com'),))
    def test_dashboard(self):
        self.user.is_superuser = False
        self.user.save()
        _admins_before = settings.ADMINS
        settings.ADMINS = (('Bob', 'bob@example.com'),)
        try:
            response = self.client.get(reverse('manage:home'))
            eq_(response.status_code, 200)
            ok_('bob@example.com' in response.content)
            # create a superuser
            self.user.is_superuser = True
            assert self.user.email
            self.user.save()
            response = self.client.get(reverse('manage:home'))
            eq_(response.status_code, 200)
            ok_('bob@example.com' not in response.content)
            ok_(self.user.email in response.content)
        finally:
            settings.ADMINS = _admins_before

    def test_cache_busting_headers(self):
        # viewing any of the public pages should NOT have it
        response = self.client.get('/')
        eq_(response.status_code, 200)
        ok_('no-store' not in response.get('Cache-Control', ''))

        response = self.client.get(reverse('manage:home'))
        eq_(response.status_code, 200)
        ok_('no-store' in response['Cache-Control'])


class TestUsersAndGroups(ManageTestCase):
    def test_user_group_pages(self):
        """User and group listing pages respond with success."""
        response = self.client.get(reverse('manage:users'))
        eq_(response.status_code, 200)
        response = self.client.get(reverse('manage:users'), {'page': 5000})
        eq_(response.status_code, 200)
        response = self.client.get(reverse('manage:groups'))
        eq_(response.status_code, 200)

    def test_user_edit(self):
        """Add superuser and staff status via the user edit form."""
        user = User.objects.create_user('no', 'no@no.com', 'no')
        response = self.client.post(
            reverse('manage:user_edit', kwargs={'id': user.id}),
            {
                'is_superuser': 'on',
                'is_staff': 'on',
                'is_active': 'on'
            }
        )
        self.assertRedirects(response, reverse('manage:users'))
        user = User.objects.get(id=user.id)
        ok_(user.is_superuser)
        ok_(user.is_staff)

    def test_group_add(self):
        """Add a group and verify its creation."""
        response = self.client.get(reverse('manage:group_new'))
        eq_(response.status_code, 200)
        response = self.client.post(
            reverse('manage:group_new'),
            {
                'name': 'fake_group'
            }
        )
        self.assertRedirects(response, reverse('manage:groups'))
        group = Group.objects.get(name='fake_group')
        ok_(group is not None)
        eq_(group.name, 'fake_group')

    def test_group_edit(self):
        """Group editing: group name change form sucessfully changes name."""
        group, __ = Group.objects.get_or_create(name='testergroup')
        response = self.client.get(reverse('manage:group_edit',
                                           kwargs={'id': group.id}))
        eq_(response.status_code, 200)
        response = self.client.post(
            reverse('manage:group_edit', kwargs={'id': group.id}),
            {
                'name': 'newtestergroup  '
            }
        )
        self.assertRedirects(response, reverse('manage:groups'))
        group = Group.objects.get(id=group.id)
        eq_(group.name, 'newtestergroup')

    def test_group_remove(self):
        group, __ = Group.objects.get_or_create(name='testergroup')
        self._delete_test(group, 'manage:group_remove', 'manage:groups')

    def test_user_search(self):
        """Searching for a created user redirects properly; otherwise fail."""
        user = User.objects.create_user('t', 'testuser@mozilla.com')
        response_ok = self.client.post(
            reverse('manage:users'),
            {
                'email': user.email
            }
        )
        self.assertRedirects(
            response_ok,
            reverse('manage:user_edit', kwargs={'id': user.id})
        )
        response_fail = self.client.post(
            reverse('manage:users'),
            {
                'email': 'bademail@mozilla.com'
            }
        )
        eq_(response_fail.status_code, 200)


class TestEvents(ManageTestCase):
    event_base_data = {
        'status': Event.STATUS_SCHEDULED,
        'description': '...',
        'participants': 'Tim Mickel',
        'privacy': 'public',
        'location': '1',
        'category': '7',
        'tags': 'xxx',
        'template': '1',
        'start_time': '2012-3-4 12:00',
        'timezone': 'US/Pacific'
    }
    placeholder = 'airmozilla/manage/tests/firefox.png'

    def test_event_request(self):
        """Event request responses and successful creation in the db."""
        response = self.client.get(reverse('manage:event_request'))
        eq_(response.status_code, 200)
        with open(self.placeholder) as fp:
            response_ok = self.client.post(
                reverse('manage:event_request'),
                dict(self.event_base_data, placeholder_img=fp,
                     title='Airmozilla Launch Test')
            )
            response_fail = self.client.post(
                reverse('manage:event_request'),
                {
                    'title': 'Test fails, not enough data!',
                }
            )
            response_cancel = self.client.post(
                reverse('manage:event_request'),
                {
                    'cancel': 'yes'
                }
            )

        self.assertRedirects(response_ok, reverse('manage:events'))
        eq_(response_fail.status_code, 200)
        event = Event.objects.get(title='Airmozilla Launch Test')
        eq_(event.location, Location.objects.get(id=1))
        eq_(event.creator, self.user)
        eq_(response_cancel.status_code, 302)
        self.assertRedirects(response_cancel, reverse('manage:events'))

    def test_event_request_sorted_dropdowns(self):
        # first create a bunch of categories
        names = ['Category%s' % x for x in range(5)]
        random.shuffle(names)
        [Category.objects.create(name=x) for x in names]

        response = self.client.get(reverse('manage:event_request'))
        # the order matters
        c = response.content
        ok_(-1 <
            c.find('>Category2<') <
            c.find('>Category3<') <
            c.find('>Category4<'))

    def test_event_request_with_approvals(self):
        group1, = Group.objects.all()
        group2 = Group.objects.create(name='Group2')
        permission = Permission.objects.get(codename='change_approval')
        group1.permissions.add(permission)
        group2.permissions.add(permission)
        group_user = User.objects.create_user(
            'username',
            'em@ail.com',
            'secret'
        )
        group_user.groups.add(group2)

        long_description_with_html = (
            'The researchers took a "theoretical" approach instead, using '
            'something known as the no-signalling conditions. They '
            'considered an entangled system with a set of independent '
            'physical attributes, some observable, some hidden variables. '
            '\n\n'
            'Next, they allowed the state of the hidden variables '
            'to propagate faster than the speed of light, which let '
            'them influence the measurements on the separated pieces of '
            'the experiment. '
            '\n\n'
            'Baskin & Robbins'
        )

        with open(self.placeholder) as fp:
            response = self.client.post(
                reverse('manage:event_request'),
                dict(self.event_base_data,
                     description=long_description_with_html,
                     placeholder_img=fp,
                     title='Airmozilla Launch Test',
                     approvals=[group1.pk, group2.pk])
            )
            eq_(response.status_code, 302)
        event = Event.objects.get(title='Airmozilla Launch Test')
        approvals = event.approval_set.all()
        eq_(approvals.count(), 2)
        # this should send an email to all people in those groups
        email_sent = mail.outbox[-1]
        ok_(group_user.email in email_sent.to)
        ok_(event.title in email_sent.subject)
        ok_(reverse('manage:approvals') in email_sent.body)
        ok_('Baskin & Robbins' in email_sent.body)  # & not &amp;
        # edit it and drop the second group
        response_ok = self.client.post(
            reverse('manage:event_edit', kwargs={'id': event.id}),
            dict(self.event_base_data, title='Different title',
                 approvals=[group1.pk])
        )
        eq_(response_ok.status_code, 302)
        event = Event.objects.get(title='Different title')
        approvals = event.approval_set.all()
        eq_(approvals.count(), 1)

    def test_tag_autocomplete(self):
        """Autocomplete makes JSON for fixture tags and a nonexistent tag."""
        response = self.client.get(
            reverse('manage:tag_autocomplete'),
            {
                'q': 'tes'
            }
        )
        eq_(response.status_code, 200)
        parsed = json.loads(response.content)
        ok_('tags' in parsed)
        tags = [t['text'] for t in parsed['tags'] if 'text' in t]
        eq_(len(tags), 3)
        ok_(('tes' in tags) and ('test' in tags) and ('testing' in tags))

    def test_participant_autocomplete(self):
        """Autocomplete makes JSON pages and correct results for fixtures."""
        response = self.client.get(
            reverse('manage:participant_autocomplete'),
            {
                'q': 'Ti'
            }
        )
        eq_(response.status_code, 200)
        parsed = json.loads(response.content)
        ok_('participants' in parsed)
        participants = [p['text'] for p in parsed['participants']
                        if 'text' in p]
        eq_(len(participants), 1)
        ok_('Tim Mickel' in participants)
        response_fail = self.client.get(
            reverse('manage:participant_autocomplete'),
            {
                'q': 'ickel'
            }
        )
        eq_(response_fail.status_code, 200)
        parsed_fail = json.loads(response_fail.content)
        eq_(parsed_fail, {'participants': []})
        response_blank = self.client.get(
            reverse('manage:participant_autocomplete'),
            {
                'q': ''
            }
        )
        eq_(response_blank.status_code, 200)
        parsed_blank = json.loads(response_blank.content)
        eq_(parsed_blank, {'participants': []})

    def test_events(self):
        """The events page responds successfully."""
        response = self.client.get(reverse('manage:events'))
        eq_(response.status_code, 200)

    def test_find_event(self):
        """Find event responds with filtered results or raises error."""
        response_ok = self.client.post(reverse('manage:events'),
                                       {'title': 'test'})
        eq_(response_ok.status_code, 200)
        ok_(response_ok.content.find('Test event') >= 0)
        response_fail = self.client.post(reverse('manage:events'),
                                         {'title': 'laskdjflkajdsf'})
        eq_(response_fail.status_code, 200)
        ok_(response_fail.content.find('No event') >= 0)

    def test_event_edit_slug(self):
        """Test editing an event - modifying an event's slug
           results in a correct EventOldSlug."""
        event = Event.objects.get(title='Test event')
        response = self.client.get(reverse('manage:event_edit',
                                           kwargs={'id': event.id}))
        eq_(response.status_code, 200)
        response_ok = self.client.post(
            reverse('manage:event_edit', kwargs={'id': event.id}),
            dict(self.event_base_data, title='Tested event')
        )
        self.assertRedirects(response_ok, reverse('manage:events'))
        ok_(EventOldSlug.objects.get(slug='test-event', event=event))
        event = Event.objects.get(title='Tested event')
        eq_(event.slug, 'tested-event')
        eq_(event.modified_user, self.user)
        response_fail = self.client.post(
            reverse('manage:event_edit', kwargs={'id': event.id}),
            {
                'title': 'not nearly enough data',
                'status': Event.STATUS_SCHEDULED
            }
        )
        eq_(response_fail.status_code, 200)

    def test_event_edit_templates(self):
        """Event editing results in correct template environments."""
        event = Event.objects.get(title='Test event')
        url = reverse('manage:event_edit', kwargs={'id': event.id})
        response_ok = self.client.post(
            url,
            dict(self.event_base_data, title='template edit',
                 template_environment='tv1=\'hi\'\ntv2===')
        )
        self.assertRedirects(response_ok, reverse('manage:events'))
        event = Event.objects.get(id=event.id)
        eq_(event.template_environment, {'tv1': "'hi'", 'tv2': '=='})
        response_edit_page = self.client.get(url)
        eq_(response_edit_page.status_code, 200,
            'Edit page renders OK with a specified template environment.')
        response_fail = self.client.post(
            url,
            dict(self.event_base_data, title='template edit',
                 template_environment='failenvironment')
        )
        eq_(response_fail.status_code, 200)

    def test_timezones(self):
        """Event requests/edits demonstrate correct timezone math."""

        def _tz_test(url, tzdata, correct_date, msg):
            with open(self.placeholder) as fp:
                base_data = dict(self.event_base_data,
                                 title='timezone test', placeholder_img=fp)
                self.client.post(url, dict(base_data, **tzdata))
            event = Event.objects.get(title='timezone test',
                                      start_time=correct_date)
            ok_(event, msg + ' vs. ' + str(event.start_time))
        url = reverse('manage:event_request')
        _tz_test(
            url,
            {
                'start_time': '2012-08-03 12:00',
                'timezone': 'US/Eastern'
            },
            datetime.datetime(2012, 8, 3, 16).replace(tzinfo=utc),
            'Event request summer date - Eastern UTC-04 input'
        )
        _tz_test(
            url,
            {
                'start_time': '2012-11-30 3:00',
                'timezone': 'Europe/Paris'
            },
            datetime.datetime(2012, 11, 30, 2).replace(tzinfo=utc),
            'Event request winter date - CET UTC+01 input'
        )
        event = Event.objects.get(title='Test event')
        url = reverse('manage:event_edit', kwargs={'id': event.id})
        _tz_test(
            url,
            {
                'start_time': '2012-08-03 15:00',
                'timezone': 'US/Pacific'
            },
            datetime.datetime(2012, 8, 3, 22).replace(tzinfo=utc),
            'Modify event summer date - Pacific UTC-07 input'
        )
        _tz_test(
            url,
            {
                'start_time': '2012-12-25 15:00',
                'timezone': 'US/Pacific'
            },
            datetime.datetime(2012, 12, 25, 23).replace(tzinfo=utc),
            'Modify event winter date - Pacific UTC-08 input'
        )

    def test_event_archive(self):
        """Event archive page loads and shows correct archive_time behavior."""
        event = Event.objects.get(title='Test event')
        event.archive_time = None
        event.save()
        url = reverse('manage:event_archive', kwargs={'id': event.id})
        response_ok = self.client.get(url)
        eq_(response_ok.status_code, 200)
        response_ok = self.client.post(url, {'archive_time': '120'})
        self.assertRedirects(response_ok, reverse('manage:events'))
        event_modified = Event.objects.get(id=event.id)
        now = (datetime.datetime.utcnow()
               .replace(tzinfo=utc, microsecond=0))
        # because this `now` can potentially be different in the tests
        # compared (if the tests run slow) to the views,
        # it's safer to not look at the seconds
        eq_(
            event_modified.archive_time.strftime('%d %H:%M'),
            (now + datetime.timedelta(minutes=120)).strftime('%d %H:%M')
        )

    def test_event_duplication(self):
        event = Event.objects.get(title='Test event')
        url = reverse('manage:event_duplicate', args=(event.id,))
        response = self.client.get(url)
        eq_(response.status_code, 200)
        ok_('value="Test event"' in response.content)

    def test_event_preview_shortcut(self):
        # become anonymous (reverse what setUp() does)
        self.client.logout()

        # view it anonymously
        event = Event.objects.get(title='Test event')
        url = reverse('main:event', args=(event.slug,))
        response = self.client.get(url)
        eq_(response.status_code, 200)
        edit_url = reverse('manage:event_edit', args=(event.pk,))
        ok_(edit_url not in response.content)
        # now log in
        assert self.client.login(username='fake', password='fake')
        # check that you can view the edit page
        response = self.client.get(edit_url)
        eq_(response.status_code, 200)
        # and now the real test
        response = self.client.get(url)
        eq_(response.status_code, 200)
        ok_(edit_url in response.content)

    @patch('airmozilla.base.utils.urllib2')
    def test_vidly_url_to_shortcode(self, p_urllib2):
        event = Event.objects.get(title='Test event')
        url = reverse('manage:vidly_url_to_shortcode', args=(event.pk,))

        def mocked_urlopen(request):
            return StringIO("""
            <?xml version="1.0"?>
            <Response>
              <Message>All medias have been added.</Message>
              <MessageCode>2.1</MessageCode>
              <BatchID>47520</BatchID>
              <Success>
                <MediaShortLink>
                  <SourceFile>http://www.com/file.flv</SourceFile>
                  <ShortLink>8oxv6x</ShortLink>
                  <MediaID>13969839</MediaID>
                  <QRCode>http://vid.ly/8oxv6x/qrcodeimg</QRCode>
                  <HtmlEmbed>code code</HtmlEmbed>
                  <EmailEmbed>more code code</EmailEmbed>
                </MediaShortLink>
              </Success>
            </Response>
            """)
        p_urllib2.urlopen = mocked_urlopen

        response = self.client.get(url)
        eq_(response.status_code, 405)

        response = self.client.post(url, {
            'url': 'not a url'
        })
        eq_(response.status_code, 400)

        response = self.client.post(url, {
            'url': 'http://www.com/'
        })
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content['shortcode'], '8oxv6x')

    def test_events_autocomplete(self):
        event = Event.objects.get(title='Test event')
        event2 = Event.objects.create(
            title='The Other Cool Title Event',
            description=event.description,
            start_time=event.start_time,
        )
        eq_(Event.objects.all().count(), 2)
        url = reverse('manage:event_autocomplete')

        response = self.client.get(url)
        eq_(response.status_code, 400)

        response = self.client.get(url, {'q': 'something', 'max': 'nan'})
        eq_(response.status_code, 400)

        response = self.client.get(url, {'q': 'eVEnt'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['Test event', 'The Other Cool Title Event'])

        response = self.client.get(url, {'q': 'EVen', 'max': 1})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['Test event'])

        response = self.client.get(url, {'q': 'E'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, [])

        response = self.client.get(url, {'q': 'COOL'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['The Other Cool Title Event'])

        response = self.client.get(url, {'q': 'COO'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['The Other Cool Title Event'])

        response = self.client.get(url, {'q': 'THE'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, [])

        # the autocomplete caches the same search
        event2.title = event2.title.replace('Cool', 'Brilliant')
        event2.save()

        response = self.client.get(url, {'q': 'COol'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['The Other Cool Title Event'])

        # but if the query is different it should work
        response = self.client.get(url, {'q': 'brill'})
        eq_(response.status_code, 200)
        content = json.loads(response.content)
        eq_(content, ['The Other Brilliant Title Event'])


class TestParticipants(ManageTestCase):
    def test_participant_pages(self):
        """Participants pagination always returns valid pages."""
        response = self.client.get(reverse('manage:participants'))
        eq_(response.status_code, 200)
        response = self.client.get(reverse('manage:participants'),
                                   {'page': 5000})
        eq_(response.status_code, 200)

    def test_participant_find(self):
        """Search filters participants; returns all for bad search."""
        response_ok = self.client.post(
            reverse('manage:participants'),
            {
                'name': 'Tim'
            }
        )
        eq_(response_ok.status_code, 200)
        ok_(response_ok.content.find('Tim') >= 0)
        response_fail = self.client.post(
            reverse('manage:participants'),
            {
                'name': 'Lincoln'
            }
        )
        eq_(response_fail.status_code, 200)
        ok_(response_fail.content.find('Tim') >= 0)

    def test_participant_edit(self):
        """Participant edit page responds OK; bad form results in failure;
        submission induces a change.
        """
        participant = Participant.objects.get(name='Tim Mickel')
        response = self.client.get(reverse('manage:participant_edit',
                                           kwargs={'id': participant.id}))
        eq_(response.status_code, 200)
        response_ok = self.client.post(
            reverse('manage:participant_edit', kwargs={'id': participant.id}),
            {
                'name': 'George Washington',
                'email': 'george@whitehouse.gov',
                'role': Participant.ROLE_PRINCIPAL_PRESENTER,
                'cleared': Participant.CLEARED_YES
            }
        )
        self.assertRedirects(response_ok, reverse('manage:participants'))
        participant_george = Participant.objects.get(id=participant.id)
        eq_(participant_george.name, 'George Washington')
        response_fail = self.client.post(
            reverse('manage:participant_edit', kwargs={'id': participant.id}),
            {
                'name': 'George Washington',
                'email': 'bademail'
            }
        )
        eq_(response_fail.status_code, 200)

    def test_participant_email(self):
        """Participant email page generates a token, redirects properly."""
        participant = Participant.objects.get(name='Tim Mickel')
        participant.clear_token = ''
        participant.save()
        url = reverse('manage:participant_email',
                      kwargs={'id': participant.id})
        response = self.client.get(url)
        eq_(response.status_code, 200)
        participant = Participant.objects.get(name='Tim Mickel')
        ok_(participant.clear_token)
        response_redirect = self.client.post(url)
        self.assertRedirects(response_redirect, reverse('manage:participants'))

    def test_participant_new(self):
        """New participant page responds OK and form works as expected."""
        response = self.client.get(reverse('manage:participant_new'))
        eq_(response.status_code, 200)
        with open('airmozilla/manage/tests/firefox.png') as fp:
            response_ok = self.client.post(
                reverse('manage:participant_new'),
                {
                    'name': 'Mozilla Firefox',
                    'slug': 'mozilla-firefox',
                    'photo': fp,
                    'email': 'mozilla@mozilla.com',
                    'role': Participant.ROLE_PRINCIPAL_PRESENTER,
                    'cleared': Participant.CLEARED_NO
                }
            )
        self.assertRedirects(response_ok, reverse('manage:participants'))
        participant = Participant.objects.get(name='Mozilla Firefox')
        eq_(participant.email, 'mozilla@mozilla.com')
        eq_(participant.creator, self.user)

    def test_participant_remove(self):
        participant = Participant.objects.get(name='Tim Mickel')
        self._delete_test(participant, 'manage:participant_remove',
                          'manage:participants')


class TestCategories(ManageTestCase):
    def test_categories(self):
        """ Categories listing responds OK. """
        response = self.client.get(reverse('manage:categories'))
        eq_(response.status_code, 200)

    def test_category_new(self):
        """ Category form adds new categories. """
        response_ok = self.client.post(
            reverse('manage:category_new'),
            {
                'name': 'Web Dev Talks '
            }
        )
        self.assertRedirects(response_ok, reverse('manage:categories'))
        ok_(Category.objects.get(name='Web Dev Talks'))
        response_fail = self.client.post(reverse('manage:category_new'))
        eq_(response_fail.status_code, 200)

    def test_category_edit(self):
        """Category edit"""
        category = Category.objects.get(name='testing')
        response = self.client.get(
            reverse('manage:category_edit', args=(category.pk,)),
        )
        eq_(response.status_code, 200)
        ok_('value="testing"' in response.content)
        response = self.client.post(
            reverse('manage:category_edit', args=(category.pk,)),
            {
                'name': 'different',
            }
        )
        eq_(response.status_code, 302)
        category = Category.objects.get(name='different')

    def test_category_delete(self):
        category = Category.objects.get(name='testing')
        self._delete_test(category, 'manage:category_remove',
                          'manage:categories')


class TestTemplates(ManageTestCase):
    def test_templates(self):
        """Templates listing responds OK."""
        response = self.client.get(reverse('manage:templates'))
        eq_(response.status_code, 200)

    def test_template_new(self):
        """New template form responds OK and results in a new template."""
        url = reverse('manage:template_new')
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'name': 'happy template',
            'content': 'hello!'
        })
        self.assertRedirects(response_ok, reverse('manage:templates'))
        ok_(Template.objects.get(name='happy template'))
        response_fail = self.client.post(url)
        eq_(response_fail.status_code, 200)

    def test_template_edit(self):
        """Template editor response OK, results in changed data or fail."""
        template = Template.objects.get(name='test template')
        url = reverse('manage:template_edit', kwargs={'id': template.id})
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'name': 'new name',
            'content': 'new content'
        })
        self.assertRedirects(response_ok, reverse('manage:templates'))
        template = Template.objects.get(id=template.id)
        eq_(template.content, 'new content')
        response_fail = self.client.post(url, {
            'name': 'no content'
        })
        eq_(response_fail.status_code, 200)

    def test_template_remove(self):
        template = Template.objects.get(name='test template')
        self._delete_test(template, 'manage:template_remove',
                          'manage:templates')

    def test_template_env_autofill(self):
        """The JSON autofiller responds correctly for the fixture template."""
        template = Template.objects.get(name='test template')
        response = self.client.get(reverse('manage:template_env_autofill'),
                                   {'template': template.id})
        eq_(response.status_code, 200)
        template_parsed = json.loads(response.content)
        ok_(template_parsed)
        eq_(template_parsed, {'variables': 'tv1=\ntv2='})


class TestApprovals(ManageTestCase):
    def test_approvals(self):
        event = Event.objects.get(title='Test event')
        group = Group.objects.get(name='testapprover')
        Approval.objects.create(event=event, group=group)

        response = self.client.get(reverse('manage:approvals'))
        eq_(response.status_code, 200)
        # if you access the approvals page without belonging to any group
        # you'll get a warning alert
        ok_('You are not a member of any group' in response.content)
        ok_('Test event' not in response.content)

        # belong to a group
        self.user.groups.add(group)
        response = self.client.get(reverse('manage:approvals'))
        eq_(response.status_code, 200)
        ok_('You are not a member of any group' not in response.content)
        ok_('Test event' in response.content)

        # but it shouldn't appear if it's removed
        event.status = Event.STATUS_REMOVED
        event.save()
        response = self.client.get(reverse('manage:approvals'))
        eq_(response.status_code, 200)
        ok_('Test event' not in response.content)

    def test_approval_review(self):
        event = Event.objects.get(title='Test event')
        group = Group.objects.get(name='testapprover')
        app = Approval.objects.create(event=event, group=group)

        url = reverse('manage:approval_review', kwargs={'id': app.id})
        response_not_in_group = self.client.get(url)
        self.assertRedirects(response_not_in_group,
                             reverse('manage:approvals'))
        User.objects.get(username='fake').groups.add(1)
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_approve = self.client.post(url, {'approve': 'approve'})
        self.assertRedirects(response_approve, reverse('manage:approvals'))
        app = Approval.objects.get(id=app.id)
        ok_(app.approved)
        ok_(app.processed)
        eq_(app.user, User.objects.get(username='fake'))


class TestLocations(ManageTestCase):
    def test_locations(self):
        """Location management pages return successfully."""
        response = self.client.get(reverse('manage:locations'))
        eq_(response.status_code, 200)

    def test_location_new(self):
        """Adding new location works correctly."""
        url = reverse('manage:location_new')
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'name': 'testing',
            'timezone': 'US/Pacific'
        })
        self.assertRedirects(response_ok, reverse('manage:locations'))
        location = Location.objects.get(name='testing')
        eq_(location.timezone, 'US/Pacific')
        response_fail = self.client.post(url)
        eq_(response_fail.status_code, 200)

    def test_location_remove(self):
        """Removing a location works correctly and leaves associated events
           with null locations."""
        location = Location.objects.get(id=1)
        self._delete_test(location, 'manage:location_remove',
                          'manage:locations')
        event = Event.objects.get(id=22)
        eq_(event.location, None)

    def test_location_edit(self):
        """Test location editor; timezone switch works correctly."""
        url = reverse('manage:location_edit', kwargs={'id': 1})
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'name': 'eastern',
            'timezone': 'US/Eastern'
        })
        self.assertRedirects(response_ok, reverse('manage:locations'))
        location = Location.objects.get(id=1)
        eq_(location.timezone, 'US/Eastern')
        response_fail = self.client.post(url, {
            'name': 'eastern',
            'timezone': 'notatimezone'
        })
        eq_(response_fail.status_code, 200)

    def test_location_timezone(self):
        """Test timezone-ajax autofill."""
        url = reverse('manage:location_timezone')
        response_fail = self.client.get(url)
        eq_(response_fail.status_code, 404)
        response_fail = self.client.get(url, {'location': ''})
        eq_(response_fail.status_code, 404)
        response_fail = self.client.get(url, {'location': '23323'})
        eq_(response_fail.status_code, 404)
        response_ok = self.client.get(url, {'location': '1'})
        eq_(response_ok.status_code, 200)
        data = json.loads(response_ok.content)
        ok_('timezone' in data)
        eq_(data['timezone'], 'US/Pacific')


class TestManagementRoles(ManageTestCase):
    """Basic tests to ensure management roles / permissions are working."""
    fixtures = ['airmozilla/manage/tests/main_testdata.json',
                'airmozilla/manage/tests/manage_groups_testdata.json']

    def setUp(self):
        super(TestManagementRoles, self).setUp()
        self.user.is_superuser = False
        self.user.save()

    def _add_client_group(self, name):
        group = Group.objects.get(name=name)
        group.user_set.add(self.user)
        ok_(group in self.user.groups.all())

    def test_producer(self):
        """Producer can see fixture events and edit pages."""
        self._add_client_group('Producer')
        response_events = self.client.get(reverse('manage:events'))
        eq_(response_events.status_code, 200)
        ok_('Test event' in response_events.content)
        response_participants = self.client.get(reverse('manage:participants'))
        ok_(response_participants.status_code, 200)
        response_participant_edit = self.client.get(
            reverse('manage:participant_edit', kwargs={'id': 1})
        )
        eq_(response_participant_edit.status_code, 200)

    def _unprivileged_event_manager_tests(self, form_contains,
                                          form_not_contains):
        """Common tests for organizers/experienced organizers to ensure
           basic event/participant permissions are not violated."""
        response_event_request = self.client.get(
            reverse('manage:event_request')
        )
        eq_(response_event_request.status_code, 200)
        ok_(form_contains in response_event_request.content)
        ok_(form_not_contains not in response_event_request.content)
        response_events = self.client.get(reverse('manage:events'))
        eq_(response_events.status_code, 200)
        ok_('Test event' not in response_events.content,
            'Unprivileged viewer can see events which do not belong to it')
        event = Event.objects.get(title='Test event')
        event.creator = self.user
        event.save()
        response_events = self.client.get(reverse('manage:events'))
        ok_('Test event' in response_events.content,
            'Unprivileged viewer cannot see events which belong to it.')
        response_event_edit = self.client.get(reverse('manage:event_edit',
                                                      kwargs={'id': event.id}))
        ok_(form_contains in response_event_edit.content)
        ok_(form_not_contains not in response_event_edit.content)
        response_participants = self.client.get(reverse('manage:participants'))
        ok_(response_participants.status_code, 200)
        participant = Participant.objects.get(id=1)
        participant_edit_url = reverse('manage:participant_edit',
                                       kwargs={'id': participant.id})
        response_participant_edit_fail = self.client.get(participant_edit_url)
        self.assertRedirects(
            response_participant_edit_fail,
            reverse('manage:participants')
        )
        participant.creator = self.user
        participant.save()
        response_participant_edit_ok = self.client.get(participant_edit_url)
        eq_(response_participant_edit_ok.status_code, 200)

    def _unprivileged_page_tests(self, additional_pages=[]):
        """Common tests to ensure unprivileged admins do not have access to
           event or user configuration pages."""
        pages = additional_pages + [
            'manage:users',
            'manage:groups',
            'manage:categories',
            'manage:locations',
            'manage:templates'
        ]
        for page in pages:
            response = self.client.get(reverse(page))
            self.assertRedirects(response, settings.LOGIN_URL +
                                 '?next=' + reverse(page))

    def test_event_organizer(self):
        """Event organizer: ER with unprivileged form, can only edit own
           participants, can only see own events."""
        self._add_client_group('Event Organizer')
        self._unprivileged_event_manager_tests(
            form_contains='Time zone',  # EventRequestForm
            form_not_contains='Approvals'
        )
        self._unprivileged_page_tests(additional_pages=['manage:approvals'])

    def test_experienced_event_organizer(self):
        """Experienced event organizer: ER with semi-privileged form,
           can only edit own participants, can only see own events."""
        self._add_client_group('Experienced Event Organizer')
        self._unprivileged_event_manager_tests(
            form_contains='Approvals',  # EventExperiencedRequestForm
            form_not_contains='Featured'
        )
        self._unprivileged_page_tests(additional_pages=['manage:approvals'])

    def test_approver(self):
        """Approver (in this case, PR), can access the approval pages."""
        self._add_client_group('PR')
        self._unprivileged_page_tests(
            additional_pages=['manage:event_request', 'manage:events',
                              'manage:participants']
        )
        response_approvals = self.client.get(reverse('manage:approvals'))
        eq_(response_approvals.status_code, 200)


class TestFlatPages(ManageTestCase):
    def test_flatpages(self):
        response = self.client.get(reverse('manage:flatpages'))
        eq_(response.status_code, 200)

    def test_flatpage_new(self):
        url = reverse('manage:flatpage_new')
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'url': '/cool-page',
            'title': 'Cool title',
            'content': '<h4>Hello</h4>'
        })
        self.assertRedirects(response_ok, reverse('manage:flatpages'))
        flatpage = FlatPage.objects.get(url='/cool-page')
        ok_(flatpage)
        site, = flatpage.sites.all()
        eq_(site.pk, settings.SITE_ID)
        response_fail = self.client.post(url)
        eq_(response_fail.status_code, 200)

    def test_flatpage_edit(self):
        flatpage = FlatPage.objects.get(title='Test page')
        url = reverse('manage:flatpage_edit', kwargs={'id': flatpage.id})
        response = self.client.get(url)
        eq_(response.status_code, 200)
        response_ok = self.client.post(url, {
            'url': flatpage.url,
            'title': 'New test page',
            'content': '<p>New content</p>'
        })
        self.assertRedirects(response_ok, reverse('manage:flatpages'))
        flatpage = FlatPage.objects.get(id=flatpage.id)
        eq_(flatpage.content, '<p>New content</p>')
        response_fail = self.client.post(url, {
            'url': 'no title',
        })
        eq_(response_fail.status_code, 200)

    def test_flatpage_remove(self):
        flatpage = FlatPage.objects.get(title='Test page')
        self._delete_test(flatpage, 'manage:flatpage_remove',
                          'manage:flatpages')

    def test_view_flatpage(self):
        flatpage = FlatPage.objects.get(title='Test page')
        response = self.client.get('/pages%s' % flatpage.url)
        eq_(response.status_code, 200)
        ok_('Test page' in response.content)


class TestErrorAlerts(ManageTestCase):

    def test_new_template_with_error(self):
        url = reverse('manage:template_new')
        response = self.client.get(url)
        ok_('Form errors!' not in response.content)
        response = self.client.post(url, {
            'name': '',
            'content': 'hello!'
        })
        ok_('Form errors!' in response.content)
