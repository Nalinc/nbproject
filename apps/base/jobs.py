"""
jobs.py - base notification routines

Author 
    Sacha Zyto <sacha@csail.mit.edu>

License
    Copyright (c) 2010-2012 Massachusetts Institute of Technology.
    MIT License (cf. MIT-LICENSE.txt or http://www.opensource.org/licenses/mit-license.php)
"""
import sys,os
import datetime
if "." not in sys.path: 
    sys.path.append(".")
if "DJANGO_SETTINGS_MODULE" not in os.environ or __name__=="__main__": 
    os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
from django.conf import settings
import base.utils as utils, base.models as M
from django.template.loader import render_to_string
from django.core.mail import EmailMessage
from django.db.models import Max
from django.db.models.deletion import Collector
from django.db.utils import IntegrityError

VISIBILITY = {1: "Myself", 2: "Staff", 3: "Class"}

pending_inserts = []
def extract_obj(o, from_class, cut_at):
    #inspired from from  http://stackoverflow.com/a/2315053/768104
    extracted = {}
    print "pulling objects related to %s" % (o,)
    links = [rel.get_accessor_name() for rel in o._meta.get_all_related_objects()]
    for link in links:
        rel_objects = getattr(o, link).all()
        for ro in rel_objects:
            classname = ro.__class__.__name__
            if classname not in extracted: 
                extracted[classname]={}
            if ro.id not in extracted[classname]:
                extracted[classname][ro.id]=1 
                extract_obj(ro, classname, cut_at)
from django.db.models.fields.related import ForeignKey
def duplicate(objs, using_src, using_dest, special_handlers):
    #adapted from http://stackoverflow.com/a/6064096/768104    
    collector = Collector(using_src)
    collector.collect(objs)
    collector.sort()
    related_models = collector.data.keys()
    duplicate_order = reversed(related_models)
    extracted = {}
    for model in duplicate_order:
        # Find all FKs on model that point to a related_model.
        fks = []
        for f in model._meta.fields:
            if isinstance(f, ForeignKey) and f.rel.to not in related_models:
                fks.append(f)
        # Replace each `sub_obj` with a duplicate.
        if model not in collector.data:
            continue
        sub_objects = collector.data[model]
        for obj in sub_objects:
            for fk in fks:
                rel_obj = getattr(obj, fk.name)
                rel_cls = rel_obj.__class__
                if rel_cls not in extracted:
                    extracted[rel_cls]={}
                if rel_obj is not None and rel_obj.id not in extracted[rel_cls]: 
                    extracted[rel_cls][rel_obj.id]=True
                    rel_obj.save(using=using_dest)
                    #print "-> saved related object %s" % (rel_obj,)
            #now ready to insert obj: 
            if model not in extracted:
                extracted[model]={}
            if obj is not None and obj.id not in extracted[model]: 
                extracted[model][obj.id]=True
                try: 
                    obj.save(using=using_dest)            
                except IntegrityError as e: 
                    pending_inserts.append(obj)
        print "%s done TOTAL objects written: %s " % (model.__name__, sum([len(extracted[i]) for i in extracted]))        
    do_pending_inserts(using_dest)

def do_pending_inserts(using):
    global pending_inserts
    new_pending = []
    for o in pending_inserts: 
        try: 
            o.save(using=using)
        except IntegrityError as e: 
            new_pending.append(o)
                            
def do_extract(t_args):
    objs = [(M.Ensemble, 237), ]
    objs_src = [o[0].objects.using("default").get(pk=o[1]) for o in objs]    
    def insert_parent_comments(o, using_dest):
        ancestors = []
        c = o.parent
        while c is not None: 
            ancestors.append(c)
            c = c.parent
        for c2 in reversed(ancestors):
            c2.save(using=using_dest)        
        print "Special Comment case: inserted %s parent comments" % (len(ancestors),)
    duplicate(objs_src, "default", "sel", {M.Comment: insert_parent_comments})
    objs_dest = [o[0].objects.using("sel").get(pk=o[1]) for o in objs]  
    
    

def do_watchdog(t_args):
    when = datetime.datetime.now()
    print """


---------------------- WATCHDOG NOTIFICATIONS FOR %s -----------------""" % (when, )
    do_watchdog_longpdfprocess()
    do_watchdog_notstartedpdfprocess()
    print "--------------- END OF WATCHDOG NOTIFICATIONS FOR %s -----------------" % (when, )
   

def do_immediate(t_args):
    when = datetime.datetime.now()
    print """


---------------------- IMMEDIATE NOTIFICATIONS FOR %s -----------------""" % (when, )
    do_auth_immediate()
    do_reply_immediate()
    ##do_answerplease_immediate()
    ##do_unclear_immediate()
    do_all_immediate()
    print "--------------- END OF IMMEDIATE NOTIFICATIONS FOR %s -----------------" % (when, )
   

def do_digest(t_args):
    when = datetime.datetime.now()
    print """

---------------------- DIGEST NOTIFICATIONS FOR %s -----------------""" % (when, )
    #do_auth_digest()
    #do_reply_digest()
    ##do_answerplease_digest()
    ##do_unclear_digest()
    print "--------------- END OF DIGEST NOTIFICATIONS FOR %s -----------------" % (when, )

def do_auth_immediate():
    latestCtime = M.Comment.objects.all().aggregate(Max("ctime"))["ctime__max"]
    latestNotif = M.Notification.objects.get(type="auth_immediate")
    setting_qry =    "select coalesce(value, (select value from base_defaultsetting where name='email_confirmation_author')) from base_user u left join base_usersetting us on us.user_id=u.id and us.setting_id=(select id from base_defaultsetting where name='email_confirmation_author') where u.id=base_comment.author_id" 
    comments = M.Comment.objects.extra(select={"setting_value": setting_qry}).filter(ctime__gt=latestNotif.atime)        
    V={"reply_to": settings.SMTP_REPLY_TO, "protocol": settings.PROTOCOL, "hostname":  settings.HOSTNAME }
    for c in (o for o in comments if o.setting_value==2): #django doesn't let us filter by extra parameters yet       
        msg = render_to_string("email/msg_auth_immediate",{"V":V, "c": c, "visibility": VISIBILITY[c.type]})
        email = EmailMessage("You've posted a new note on NB...",
                msg,  
                "NB Notifications <no-reply@notabene.csail.mit.edu>", 
                (c.author.email, ), 
                (settings.EMAIL_BCC, ))
        email.send()
        try: 
            print msg
        except UnicodeEncodeError: 
            print "not displaying msg b/c of unicode issues"
    latestNotif.atime = latestCtime
    latestNotif.save()
    
def do_all_immediate():
    #send email to for all new msg in group where I'm an admin
    latestCtime = M.Comment.objects.all().aggregate(Max("ctime"))["ctime__max"]
    latestNotif = M.Notification.objects.get(type="all_immediate")
    comments = M.Comment.objects.filter(ctime__gt=latestNotif.atime, type__gt=1)        
    V={"reply_to": settings.SMTP_REPLY_TO, "protocol": settings.PROTOCOL, "hostname":  settings.HOSTNAME }
    setting_qry = "select coalesce(value, (select value from base_defaultsetting where name='email_confirmation_all')) from base_user u left join base_usersetting us on us.user_id=u.id and us.setting_id=(select id from base_defaultsetting where name='email_confirmation_all') where u.id=base_membership.user_id" 
    for c in comments:
        memberships = M.Membership.objects.extra(select={"setting_value": setting_qry}).filter(ensemble=c.location.ensemble, admin=True).exclude(user=c.author) #we don't want to send a notice to a faculty for a comment that he wrote !
        for m in (o for o in memberships if o.setting_value==2): #django doesn't let us filter by extra parameters yet
            msg = render_to_string("email/msg_all_immediate",{"V":V, "c": c, "visibility": VISIBILITY[c.type], "m": m})    
            email = EmailMessage("%s %s just wrote a comment on %s" % (c.author.firstname, c.author.lastname, c.location.source.title),
                msg,  
                "NB Notifications <no-reply@notabene.csail.mit.edu>", 
                (m.user.email, ), 
                (settings.EMAIL_BCC, ))
            email.send()              
            try: 
                print msg
            except UnicodeEncodeError: 
                print "not displaying msg b/c of unicode issues"            
    latestNotif.atime = latestCtime
    latestNotif.save()



def do_reply_immediate():
    latestCtime = M.Comment.objects.all().aggregate(Max("ctime"))["ctime__max"]
    latestNotif = M.Notification.objects.get(type="reply_immediate")
    setting_qry =    "select coalesce(value, (select value from base_defaultsetting where name='email_confirmation_reply_author')) from base_user u left join base_usersetting us on us.user_id=u.id and us.setting_id=(select id from base_defaultsetting where name='email_confirmation_reply_author') where u.id=base_comment.author_id" 
    recentComments = M.Comment.objects.filter(ctime__gt=latestNotif.atime, type=3, parent__type=3)
    V={"reply_to": settings.SMTP_REPLY_TO, "protocol": settings.PROTOCOL, "hostname":  settings.HOSTNAME }
    #TODO: This is ugly: I'd like to keep this vectorized at the DB level, but I don't know how to do it in django, hence the double forloop.
    for rc in recentComments:                     
        comments = M.Comment.objects.extra(select={"setting_value": setting_qry}).filter(location=rc.location).exclude(author=rc.author)
        emailed_uids=[] #bookkeeping in order not to email  N times someone who posted N posts in a thread !
        for c in (o for o in comments if o.setting_value==2): #django doesn't let us filter by extra parameters yet
            if c.author_id not in emailed_uids: 
                emailed_uids.append(c.author_id)
                msg =  render_to_string("email/msg_reply_immediate",{"V": V, "c":c, "rc":rc})
                email = EmailMessage("New reply on %s" % (c.location.source.title,), 
                msg, "NB Notifications <no-reply@notabene.csail.mit.edu>", (c.author.email, ),(settings.EMAIL_BCC, ))
                email.send()
                try: 
                    print msg
                except UnicodeEncodeError: 
                    print "not displaying msg b/c of unicode issues"                
    latestNotif.atime = latestCtime
    latestNotif.save()


def do_watchdog_longpdfprocess():
        minutes_ago = datetime.datetime.now() - datetime.timedelta(0, 10*60) # 10 minutes ago 
        objs = M.Processqueue.objects.filter(started__isnull=False, completed__isnull=True, started__lt=minutes_ago)
        if objs.count() > 0:
            o=objs[0]            
            V = {"processtime": datetime.datetime.now()-o.started, "o": o,  "hostname":  settings.HOSTNAME }
            msg = render_to_string("email/msg_watchdog_longpdf",V)
            recipients = [i[1] for i in settings.ADMINS]
            email = EmailMessage("NB Watchdog warning: long pdf process",
                msg,  
                "NB Watchdog <no-reply@notabene.csail.mit.edu>", 
                recipients, 
                (settings.EMAIL_BCC, ))
            email.send()
            print msg
        
def do_watchdog_notstartedpdfprocess():
        minutes_ago = datetime.datetime.now() - datetime.timedelta(0, 20*60) # 20 minutes ago 
        objs = M.Processqueue.objects.filter(started__isnull=True,  submitted__lt=minutes_ago)
        #rows =  DB.getRows("select p.id_source, s.title, now()-p.submitted from nb2_processqueue p left join source s on s.id=p.id_source where now()-p.submitted>'60 minutes' and p.started is null", ());
        if objs.count() > 0: 
            V = {"objs": objs,  "hostname":  settings.HOSTNAME }
            msg = render_to_string("email/msg_watchdog_notstartedpdf",V)
            recipients = [i[1] for i in settings.ADMINS]
            email = EmailMessage("NB Watchdog warning: some pdf processes haven't started yet",
                msg,  
                "NB Watchdog <no-reply@notabene.csail.mit.edu>", 
                recipients, 
                (settings.EMAIL_BCC, ))
            email.send()
            print msg
    
def do_auth_digest():
    latestCtime = DB.getVal("select max(ctime) from nb2_comment", ());
    rows = DB.getRows("""
select v.id, v.id_location, v.id_author, v.email, v.id_type, v.title, v.body, v.ctime, e.name, us.value as user_setting, ds.value as default_setting 
from nb2_v_comment v left join nb2_user_settings us on id_author=us.id_user and us.name='email_confirmation_author' and us.valid=1, ensemble e, nb2_default_settings ds 
where 
e.id= v.id_ensemble and 
v.ctime > (select atime from nb2_latest_notifications where type='auth_digest') 
and ds.name='email_confirmation_author' 
and ( us.value = 1 or (us.value is null and ds.value=1))
order by v.ctime""", ())    
    msg_by_email = {}
    for r2 in rows:
        i=1
        V={"reply_to": settings.SMTP_REPLY_TO, "protocol": settings.PROTOCOL, "hostname":  settings.HOSTNAME }
        V["id_location"] = r2[i];i+=1
        V["id_author"] = r2[i];i+=1
        V["email"] = r2[i];i+=1
        #V["visibility"];
        i+=1
        V["file"] = r2[i];i+=1
        V["body"] = r2[i];i+=1
        V["ctime"]= r2[i];i+=1
        V["ensemble"] = r2[i];i+=1
        if V["email"] not in msg_by_email:
            msg_by_email[V["email"]]=[]
        msg_by_email[V["email"]].append(V)
    DB.doTransaction("update nb2_latest_notifications set atime = ? where type='auth_digest'", (latestCtime,))
    for email in msg_by_email:
        vals = msg_by_email[email]
        msg = MSG_AUTH_DIGEST_HEADER % vals[0]
        for V in vals:
            msg+=MSG_AUTH_DIGEST_BODY % V
        msg+=MSG_AUTH_DIGEST_FOOTER
        if settings.DO_EMAIL:
            session =  smtplib.SMTP(settings.SMTP_SERVER)
            recipients = [email]
            if CC_ME:
                recipients.append(CC_EMAIL)
            smtpresult = session.sendmail(settings.SMTP_USER, recipients, msg)
        try: 
            print msg
        except UnicodeEncodeError: 
            print "not displaying msg b/c of unicode issues"
        
def do_reply_digest():
    latestCtime = DB.getVal("select max(ctime) from nb2_comment", ());
    rows = DB.getRows("""
select v.id_location, v.email, i.title, i.body, i.ctime
from nb2_v_comment v left join nb2_user_settings us on v.id_author=us.id_user and us.name='email_confirmation_reply_author' and us.valid=1,nb2_v_digest_class i, nb2_default_settings ds  
where v.id_location = i.id_location and 
v.id_author != i.id_author and 
ds.name='email_confirmation_reply_author' and 
( us.value = 1 or (us.value is null and ds.value=1))""", ())
    msg_by_email = {}
    for r2 in rows:
        i=0
        V={"reply_to": settings.SMTP_REPLY_TO, "protocol": settings.PROTOCOL, "hostname":  settings.HOSTNAME }
        V["id_location"] = r2[i];i+=1
        V["email"] = r2[i];i+=1
        V["file"] = r2[i];i+=1
        V["body"] = r2[i];i+=1
        V["ctime"]= r2[i];i+=1
        if V["email"] not in msg_by_email:
            msg_by_email[V["email"]]=[]
        msg_by_email[V["email"]].append(V)
    DB.doTransaction("update nb2_latest_notifications set atime = ? where type='reply_digest'", (latestCtime,))
    for email in msg_by_email:
        locations = []
        vals = msg_by_email[email]
        msg = MSG_REPLY_DIGEST_HEADER % vals[0]
        for V in vals:
            if V["id_location"] not in locations:
                locations.append(V["id_location"])
                msg+=MSG_REPLY_DIGEST_BODY % V
        msg+=MSG_REPLY_DIGEST_FOOTER
        if settings.DO_EMAIL:
            session =  smtplib.SMTP(settings.SMTP_SERVER)
            recipients = [email]
            if CC_ME:
                recipients.append(CC_EMAIL)
            smtpresult = session.sendmail(settings.SMTP_USER, recipients, msg)
        try: 
            print msg
        except UnicodeEncodeError: 
            print "not displaying msg b/c of unicode issues"            
        
if __name__ == "__main__" :
    ACTIONS = {
        "immediate": do_immediate,
        "digest": do_digest, 
        "watchdog": do_watchdog,
        "extract": do_extract
        }
    utils.process_cli(__file__, ACTIONS)

