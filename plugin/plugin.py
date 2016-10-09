from Plugins.Plugin import PluginDescriptor

from enigma import eTimer

from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Screens.VirtualKeyBoard import VirtualKeyBoard

from Components.PluginComponent import plugins
from Components.Label import Label
from Components.ActionMap import ActionMap
from Components.Sources.StaticText import StaticText
from Components.config import config, getConfigListEntry, ConfigSubsection, ConfigYesNo, NoSave
from Components.ConfigList import ConfigListScreen
from Components.Pixmap import Pixmap, MultiPixmap
from Components.Sources.List import List
from Components.Console import Console
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaTest
from Components.MenuList import MenuList

from Tools.Directories import fileExists, resolveFilename, SCOPE_PLUGINS, SCOPE_CURRENT_PLUGIN, SCOPE_CURRENT_SKIN
from Tools.LoadPixmap import LoadPixmap
from xml.etree.cElementTree import parse as smparse

import sys
import os

config.plugins.servicemanager = ConfigSubsection()
config.plugins.servicemanager.onSetupMenu = ConfigYesNo(default=False)
config.plugins.servicemanager.onExtensionsMenu = ConfigYesNo(default=False)

def busyboxVersion():
	version = ''
	for line in open("/var/lib/opkg/info/busybox.control", "r"):
		if line.startswith('Version:'):
			version = line.split(":",1)[1].strip()
			break
	return version

def readInetdConfFile(inetdservice):
	filename = "/etc/inetd.conf"
	state = False
	for line in open(filename, "r"):
		if line.startswith(inetdservice):
			state = True
			break
		elif line.startswith("#" + inetdservice):
			state = False
			break
	return state			# startAtBoot or ready to request

def writeInetdConfFile(service):
	filename = "/etc/inetd.conf"
	os.rename(filename, filename + ".org")
	filesource=open(filename + ".org", "r")
	filedest=open(filename, "w")
	for line in filesource:
		if line.startswith(service):
			line = "#" + line
		elif line.startswith("#" + service):
			line = line.replace('#', '')
		filedest.write(line)
	del filesource
	del filedest
	os.remove(filename + ".org")

def saveConfFile(filename, linelist):
	os.rename(filename, filename + ".org")
	filedest=open(filename, "w")
	filedest.writelines( "%s\n" % item for item in linelist )
	del filedest

class ServiceController():

	def __init__(self):
		self.Console = Console()

	def listProcesses(self, args):
		if not self.Console:
			self.Console = Console()
		self.Console.ePopen("ps", self.listProcessesFinished, args)

	def listProcessesFinished(self, result, retval, args):
		(callback) = args[0]
		srvlist = args[1]
		if result:
			for srv in srvlist:
				srv['state'] = False
				for line in result.splitlines():
					tokens = line.split()
					name = tokens[4].strip()
					if name.startswith("/"):			# split path
						(path, name) = os.path.split(name)
					if name.endswith(":"):				# avahi-daemon(:)
						name = name.split(":")[0]
					if name == srv['demon']:
						srv['state'] = True
						break
				print "[ServiceController] service: %s  state: %s" % (srv['name'], srv['state'])
			callback(srvlist)

	def runCmd(self, cmd, callback=None):
		if not self.Console:
			self.Console = Console()
		self.Console.ePopen(cmd, self.runCmdFinished, callback)

	def runCmdFinished(self, result, retval, callback):
		if callback is not None:
			(callback) = callback
			if result:
				callback(result.strip())
			else:
				callback("Done")
			print "[ServiceController] result:", result.strip()

class ServiceControlPanel(Screen, ConfigListScreen):

	skin = """
  <screen name="ServiceControlPanel" position="fill" flags="wfNoBorder">
    <panel name="PigTemplate"/>
    <panel name="ButtonTemplate_RGYBS"/>
    <widget name="version" position="590,120" size="500,40" font="Regular;24" />
    <widget name="statetext" position="590,180" size="100,40" font="Regular;24" />
    <widget name="statepic" pixmaps="/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceManager/icons/stopped.png,/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceManager/icons/pause.png,/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceManager/icons/running.png" position="750,180" zPosition="10" size="40,40" transparent="1" alphatest="on"/>
    <widget name="conffile" position="590,240" size="600,40" font="Regular;24" />
    <widget name="config" position="590,320" size="500,60" font="Regular;24" selectionPixmap="PLi-HD/buttons/sel.png" scrollbarMode="showOnDemand" />
    <widget source="menuinfo" render="Label" position="85,540" size="450,40" backgroundColor="darkgrey" transparent="1" font="Regular;20" />
  </screen>"""

	def __init__(self, session, service):
		Screen.__init__(self, session)
		self.session = session
		self.service = service
		self.setup_title = _("%s Control Panel" % self.service['name'])
		print "[ServiceControlPanel] open service panel:", self.service

		self.list = [ ]
		ConfigListScreen.__init__(self, self.list, session = session)
		self.startAtBootEntry = None
		self.start_at_boot = False
#"SetupActions", "MenuActions", 
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"],
			{
				"ok": self.applyBootSetting,
				"cancel": self.keyCancel,
				"red": self.stopService,
				"green": self.startService,
				"yellow": self.restartService,
				"blue": self.editConfigFile,
			}, -2)

		self["version"] = Label("Version:   %s" % self.service['version'])
		self["statetext"] = Label(_("State:"))
		self["conffile"] = Label("")
		self["statepic"] = MultiPixmap()
		self["statepic"].hide()

		self["key_red"] = StaticText(_("Stop"))
		self["key_green"] = StaticText(_("Start"))
		self["key_yellow"] = StaticText(_("Restart"))
		self["key_blue"] = StaticText("")

		if self.service.has_key('conffile'):
			self["conffile"].setText(_("Config file:  %s") % self.service['conffile'])
			self["key_blue"] = StaticText(_("Config"))
			
		self["menuinfo"] = StaticText("")

		self.sc = ServiceController()
		self.update_state_timer = eTimer()
		self.update_state_timer.callback.append(self.updateServiceState)

		self.getServiceBootSetting()
		self.onLayoutFinish.append(self.layoutFinished)

	def layoutFinished(self):
		self.setTitle(self.setup_title)
		self.updateStatePic(self.service['state'])

	def updateStatePic(self, state):
		if state:
			self["statepic"].setPixmapNum(2)
		elif self.service['name'] == "Telnet" and readInetdConfFile("telnet"):
			self["statepic"].setPixmapNum(1)
		elif self.service['name'] == "Vsftpd" and readInetdConfFile("ftp"):
			self["statepic"].setPixmapNum(1)
		else:
			self["statepic"].setPixmapNum(0)
		self["statepic"].show()

	def updateServiceStateFinished(self, data):
		if data:
			self.service = data[0]
			print "[ServiceControlPanel] service: %s  state: %s" % (self.service['name'], self.service['state'])
			self.updateStatePic(self.service['state'])

	def updateServiceState(self):
		if self.service.has_key('pidfile'):
			self.service['state'] = fileExists(self.service['pidfile'])
			self.updateStatePic(self.service['state'])
		else:
			self.sc.listProcesses([self.updateServiceStateFinished, [self.service]])

	def getServiceBootSettingFinished(self, data):
		if data:
			self.start_at_boot = "System" in data.split()
			self.createBootConfigEntry()

	def getServiceBootSetting(self):
		initmode = False
		if self.service['name'] == "Telnet":
			self.start_at_boot = readInetdConfFile("telnet")
		elif self.service['name'] == "Vsftpd":
			self.start_at_boot = readInetdConfFile("ftp")
		elif self.service['name'] == "Samba":
			self.start_at_boot = fileExists("/etc/network/if-up.d/01samba-start")
		elif self.service.has_key('initscript'):
			initmode = True
			self.sc.runCmd("update-rc.d -n %s defaults" % self.service['initscript'], self.getServiceBootSettingFinished)
		if not initmode:
			self.createBootConfigEntry()

	def createBootConfigEntry(self):
		self.list = [ ]
		self.startAtBootEntry = NoSave(ConfigYesNo(default=self.start_at_boot))
		self.list.append(getConfigListEntry(_("Start %s at boot") % self.service['name'], self.startAtBootEntry))
		self["config"].list = self.list
		self["config"].l.setList(self.list)

	def updateInfoLabel(self):
		if self["config"].isChanged():
			self["menuinfo"].setText(_("Press OK to save boot config."))
		else:
			self["menuinfo"].setText("")

	def keyLeft(self):
		ConfigListScreen.keyLeft(self)
		self.updateInfoLabel()

	def keyRight(self):
		ConfigListScreen.keyRight(self)
		self.updateInfoLabel()

	def runMsg(self, retval):
		ok = False
		if self.service['name'] == "Telnet":
			if readInetdConfFile("telnet") and self.action == "start" or self.action == "restart":
				ok=True
			elif not readInetdConfFile("telnet") and self.action == "stop":
				ok=True
		elif self.service['name'] == "Vsftpd":
			if readInetdConfFile("ftp") and self.action == "start" or self.action == "restart":
				ok=True
			elif not readInetdConfFile("ftp") and self.action == "stop":
				ok=True
		elif self.action == "restart" and self.service['state'] or self.action == "start" and self.service['state'] or self.action == "stop" and not self.service['state']:
			ok=True
		if ok:
			self.msg = self.session.open(MessageBox, _("Done."), MessageBox.TYPE_INFO, timeout = 2)
		else:
			self.msg = self.session.open(MessageBox, _("Error. Could not %s %s" % (self.action, self.service['name'])), MessageBox.TYPE_ERROR, timeout = 3)			
		self.msg.setTitle(self.setup_title)

	def startStopInetdService(self, inetdservice, action):
		if action == "start" and not readInetdConfFile(inetdservice) or action == "stop" and readInetdConfFile(inetdservice):
			writeInetdConfFile(inetdservice)
		self.sc.runCmd("killall -HUP inetd", self.runCmdFinished)

	def runServiceScripts(self, action):
		servicescripts = self.service['servicescripts'].split(',')
		if self.service['name'] == "Samba" and not self.start_at_boot:
			servicescripts = ["/etc/network/01samba-kill", "/etc/network/01samba-start"]
		if action == "stop":
			self.sc.runCmd(servicescripts[0], self.runCmdFinished)
		elif action == "start":
			self.sc.runCmd(servicescripts[1], self.runCmdFinished)
		elif action == "restart":
			self.sc.runCmd(servicescripts[0], self.runCmdFinished)
			self.sc.runCmd(servicescripts[1], self.runCmdFinished)

	def runCustomScript(self, action):
		self.sc.runCmd(self.service['servicescript'].join(action), self.runCmdFinished)

	def runInitScript(self, action):
		cmd = "/etc/init.d/%s %s" % (self.service['initscript'], action)
		self.sc.runCmd(cmd, self.runCmdFinished)

	def runCmdFinished(self, data):
		if data:		# set time point - analize output?????
			print "[ServiceControlPanel] start/stop cmd finished"
#			if "usage" in data.split():
#				self.session.open(MessageBox, _("Check %s init script!" % self.service['initscript']), MessageBox.TYPE_ERROR, timeout = 5)

	def startStopService(self, action):
		self.action = action
		action_msg = _("Service: %s\nAction: %s" % (self.service['name'], action))
		self.msg = self.session.openWithCallback(self.runMsg, MessageBox, action_msg, MessageBox.TYPE_INFO, timeout=3, enable_input=False)
		self.msg.setTitle(self.setup_title)

		if self.service['name'] == "Telnet":
			self.startStopInetdService("telnet", action)
		elif self.service['name'] == "Vsftpd":
			self.startStopInetdService("ftp", action)
		elif self.service.has_key('servicescripts'):
			self.runServiceScripts(action)
		elif self.service.has_key('customscript'):
			self.runCustomScript(action)
		elif self.service.has_key('initscript'):
			self.runInitScript(action)

		self.update_state_timer.start(500, True)

	def startService(self):
		if self.service['state']:
			self.startStopService("restart")
		else:
			self.startStopService("start")

	def stopService(self):
		self.startStopService("stop")

	def restartService(self):
		self.startStopService("restart")

	def moveSambaScripts(self, value):
		if value:
			cmds = ("mv -f /etc/network/01samba-start /etc/network/if-up.d/", "mv -f /etc/network/01samba-kill /etc/network/if-down.d/")
		else:
			cmds = ("mv -f /etc/network/if-up.d/01samba-start /etc/network/", "mv -f /etc/network/if-down.d/01samba-kill /etc/network/")
		for x in cmds:
			self.sc.runCmd(x) 		

	def saveBootSetting(self):
		newvalue = self["config"].getCurrent()[1].value
		if self.service['name'] == "Telnet":
			writeInetdConfFile("telnet")
		elif self.service['name'] == "Vsftpd":
			writeInetdConfFile("ftp")
		elif self.service['name'] == "Samba":
			self.moveSambaScripts(newvalue)
		elif self.service.has_key('initscript'):
			if newvalue:
				init_cmd = "update-rc.d %s defaults"
			else:
				init_cmd = "update-rc.d -f %s remove"
			self.sc.runCmd(init_cmd % self.service['initscript'])			

	def applyBootSetting(self):
		if self["config"].isChanged():
			self.saveBootSetting()
			self.session.open(MessageBox, _("Boot startup setting saved."), MessageBox.TYPE_INFO, timeout = 3)
			self.close(self.service['state'])

	def cancelConfirm(self, confirmed):
		if confirmed:
			self.close(self.service['state'])

	def keyCancel(self):
		if self["config"].isChanged():
			self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Really close without saving settings?"), MessageBox.TYPE_YESNO, timeout = 10, default = True)
		else:
			self.close(self.service['state'])
		
	def editConfigFile(self):
		if self.service.has_key('conffile'):
			self.session.open(ServiceConfigEdit, self.service)

class ServiceConfigEdit(Screen):

	skin = """
  <screen name="ServiceConfigEdit" position="fill" flags="wfNoBorder">
    <panel name="PigTemplate"/>
    <panel name="ButtonTemplate_RGS"/>
    <widget name="list" position="540,110" size="660,510" font="Regular;20" />
    <widget source="menuinfo" render="Label" position="85,540" size="450,40" backgroundColor="darkgrey" transparent="1" font="Regular;20" />
  </screen>"""

	def __init__(self, session, service):
		Screen.__init__(self, session)
		self.service = service
		self.list = []

		try:
			self.list = open(self.service['conffile'], "r").read().splitlines()
		except:
			print "[ServiceConfigEdit] could not read config file:", self.service['conffile']
			self.list.append("Error reading config file:", self.service['conffile'])

		title = _("%s Config Editor") % self.service['name']
		self.setTitle(title)

		self["list"] = MenuList(list=self.list, enableWrapAround=True)

		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"],
			{
				"ok": self.editLine,
				"cancel": self.close,
				"red": self.close,
				"green": self.save,
			}, -2)

		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText(_("Save"))

		self["menuinfo"] = StaticText(_("Press OK to edit config line"))

	def editLine(self):
		self.current = self["list"].getCurrent() or ""
		self.session.openWithCallback(self.editLineCallback, VirtualKeyBoard, title="Edit text line", text=self.current)

	def editLineCallback(self, linechanged):
		if linechanged:
			index = self["list"].getSelectionIndex()
			for line in self.list:
				if line == self.current:
					self.list[index] = linechanged
					break
			self["list"].setList(self.list)

	def save(self):
		saveConfFile(self.service['conffile'], self.list)
		self.session.open(MessageBox, _("Config file changes saved."), MessageBox.TYPE_INFO, timeout = 3)

class ServiceCenterSetup(Screen, ConfigListScreen):

	skin = """
  <screen name="ServiceCenterSetup" position="fill" title="Service Center Setup" flags="wfNoBorder">
    <panel name="PigTemplate"/>
    <panel name="ButtonTemplate_RGS"/>
    <widget name="config" position="590,110" size="600,510" selectionPixmap="PLi-HD/buttons/sel.png" scrollbarMode="showOnDemand" />
  </screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.session = session

		self.list = [ ]
		ConfigListScreen.__init__(self, self.list, session = session)
		self.setup_title = _("Service Control Center Setup")

		self["actions"] = ActionMap(["SetupActions", "MenuActions"],
			{
				"cancel": self.keyCancel,
				"save": self.apply,
				"menu": self.keyCancel,
			}, -2)

		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText(_("Save"))

		self.createSetup()
		self.onLayoutFinish.append(self.layoutFinished)

	def layoutFinished(self):
		self.setTitle(self.setup_title)

	def createSetup(self):
		self.list = [ ]
		self.list.append(getConfigListEntry(_("show service manager in setup menu"), config.plugins.servicemanager.onSetupMenu))
		self.list.append(getConfigListEntry(_("show service manager in extensions menu"), config.plugins.servicemanager.onExtensionsMenu))
		self["config"].list = self.list
		self["config"].l.setSeperation(400)
		self["config"].l.setList(self.list)

	def keyLeft(self):
		ConfigListScreen.keyLeft(self)

	def keyRight(self):
		ConfigListScreen.keyRight(self)

	def confirm(self, confirmed):
		if not confirmed:
			return
		self.keySave()
		plugins.clearPluginList()
		plugins.readPluginList(resolveFilename(SCOPE_PLUGINS))

	def apply(self):
		self.session.openWithCallback(self.confirm, MessageBox, _("Use these settings?"), MessageBox.TYPE_YESNO, timeout = 20, default = True)

	def cancelConfirm(self, result):
		if not result:
			return
		for x in self["config"].list:
			x[1].cancel()
		self.close()

	def keyCancel(self):
		if self["config"].isChanged():
			self.session.openWithCallback(self.cancelConfirm, MessageBox, _("Really close without saving settings?"), MessageBox.TYPE_YESNO, timeout = 10, default = True)
		else:
			self.close()

	def changedEntry(self):
		for x in self.onChangedEntry:
			x()
		self.selectionChanged()

	def getCurrentEntry(self):
		return self["config"].getCurrent()[0]

	def getCurrentValue(self):
		return str(self["config"].getCurrent()[1].value)

	def createSummary(self):
		from Screens.Setup import SetupSummary
		return SetupSummary

class ServiceCenter(Screen):

	skin = """
  <screen name="ServiceCenter" position="fill" title="Service Control Center" flags="wfNoBorder">
    <panel name="PigTemplate"/>
    <panel name="KeyMenuTemplate"/>
    <panel name="ButtonTemplate_RGYS"/>   
    <widget source="list" render="Listbox" position="540,145" size="660,420" zPosition="3" transparent="1" scrollbarMode="showOnDemand" selectionPixmap="PLi-HD/buttons/sel.png">
	<convert type="TemplatedMultiContent">
		{"template": [
		MultiContentEntryText(pos = (5,1), size = (440,24), font=0, flags = RT_HALIGN_LEFT, text = 0), # index 0 is the service name
	 	MultiContentEntryText(pos = (5,31), size = (520,24), font=1, flags = RT_HALIGN_LEFT, text = 1), # index 1 is the service description
		MultiContentEntryPixmapAlphaTest(pos = (520,6), size = (48,48), png = 2), # index 2 is the installed status pixmap
		MultiContentEntryPixmapAlphaTest(pos = (585,20), size = (35,20), png = 3), # index 3 is the running state pixmap
		MultiContentEntryPixmapAlphaTest(pos = (0,57), size = (630,2), png = 4), # index 4 is the div pixmap
		],
		"fonts": [gFont("Regular",22),gFont("Regular",18)],
		"itemHeight": 60
		}
      </convert>
    </widget>
    <widget source="status" render="Label" position="85,385" size="450,140" backgroundColor="darkgrey" transparent="1" font="Regular;20" />
    <widget source="menuinfo" render="Label" position="85,540" size="450,40" backgroundColor="darkgrey" transparent="1" font="Regular;20" />
  </screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.session = session

		self.list = []
		self.index = None
		self.serviceList = []
		self.running_view = False
		self["list"] = List(self.list)

		self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "SetupActions", "MenuActions"],
		{
			"ok": self.selectService,
			"cancel": self.close,
			"red": self.close,
			"yellow": self.switchList,
			"green": self.selectService,
			"menu": self.pluginsetup,
		}, -2)

		self["key_red"] = StaticText(_("Close"))
		self["key_green"] = StaticText("OK")
		self["key_yellow"] = StaticText("View running")
		self["status"] = StaticText("")
		self["menuinfo"] = StaticText(_("Press MENU for plugin setup"))

		self.sc = ServiceController()

		self.createServiceList()
		if len(self.serviceList):
			self.checkServiceListStatus(self.serviceList)
			self.getPkgInfo()
			self.updateServiceListState()			

		self["list"].onSelectionChanged.append(self.selectionChanged)

	def selectionChanged(self):
		current = self["list"].getCurrent()[5]
		text = "Service %s" % current['name']
		if current['status']:
			text += "\n\n                    >>  installed"
			if current['state']:
				if current['name'] == "Telnet" or current['name'] == "Vsftpd":
					text += "\n                    >>  active connections"
				else:
					text += "\n                    >>  running"
			else:
				if current['name'] == "Telnet" and readInetdConfFile("telnet"):
					text += "\n                    >>  ready to requests"
				elif current['name'] == "Vsftpd" and readInetdConfFile("ftp"):
					text += "\n                    >>  ready to requests"
				else:
					text += "\n                    >>  not running"
			text += "\n\nPress OK to open %s control panel" % current['name']
		else:
			text += " not installed!\n\nPress OK to install it now."
		self['status'].setText(text)

	def addKeys(self, service):
		service['status'] = False
		service['state'] = False
		service['version'] = "N/A"
		return service

	def createServiceList(self):
		try:
			filename = resolveFilename(SCOPE_CURRENT_PLUGIN, "SystemPlugins/ServiceManager/services.xml")
			tree = smparse(filename).getroot()
			for service in tree.findall("service"):
				self.serviceList.append(self.addKeys(service.attrib))
			print "[ServiceManager] servicelist length:", len(self.serviceList)
		except:
			print "[ServiceManager] could not read sm config file: 'services.xml'"

	def checkServiceListStatus(self, services):
		try:
			statusfile = open("/var/lib/opkg/status", "r").read()
			for srv in services:
				checkline = "Package: %s" % srv['package']
				srv['status'] = checkline in statusfile
#				print "[ServiceManager] service: %s  status: %s" % (srv['name'] , srv['status'])
		except:
			print "[ServiceManager] could not read status file: '/var/lib/opkg/status'"

	def getPkgInfo(self):
		try:
			for srv in self.serviceList:
				if srv['status']:
					version = ''
					for line in open("/var/lib/opkg/info/%s.control" % srv['package'], "r"):
						if line.startswith('Version:'):
							version = line.split(":",1)[1].strip()
							break
					if version == busyboxVersion():
						version += "  [Busybox]"
					srv['version'] = version
#					print "[ServiceManager] service %s  version %s" % (srv['name'] , srv['version'])
		except:
			print "[ServiceManager] could not read control file: '/var/lib/opkg/info/%s.control'" % srv['package']

	def updateServiceListStateFinished(self, data):
		if data:
			self.serviceList = data
			self.updateEntryList()

	def updateServiceListState(self):
		self.sc.listProcesses([self.updateServiceListStateFinished, self.serviceList])

	def buildEntryComponent(self, service):
		div_png = LoadPixmap(cached=True, path=resolveFilename(SCOPE_CURRENT_SKIN, "skin_default/div-h.png"))
		status_png = "installable.png"
		state_png = "stopped.png"
		if service['status']:
			status_png = "installed.png"
			if service['state']:
				state_png = "running.png"
			elif service['name'] == "Telnet" and readInetdConfFile("telnet"):
				state_png = "pause.png"
			elif service['name'] == "Vsftpd" and readInetdConfFile("ftp"):
				state_png = "pause.png"

		service_status_png = LoadPixmap(cached=True, path=resolveFilename(SCOPE_CURRENT_PLUGIN, "SystemPlugins/ServiceManager/icons/%s" % status_png))
		service_state_png = LoadPixmap(cached=True, path=resolveFilename(SCOPE_CURRENT_PLUGIN, "SystemPlugins/ServiceManager/icons/%s" % state_png))

		return ((service['name'], service['description'], service_status_png, service_state_png, div_png, service))

	def somethingRunning(self):
		for service in self.serviceList:
			if service['state']:
				return True
		return False
				
	def updateEntryList(self):
		self.list = []
		self.rlist = []
		for service in self.serviceList:
			if service['state']:
				self.rlist.append(self.buildEntryComponent(service))
			self.list.append(self.buildEntryComponent(service))
		if len(self.rlist) == 0:
			self["key_yellow"].setText("")
		elif self.running_view:
			self["key_yellow"].setText("View all")
			self.list = self.rlist
		else:
			self["key_yellow"].setText("View running")
		self['list'].setList(self.list)
		self['list'].updateList(self.list)
		if self.index is not None:
			self["list"].setIndex(self.index)
			self.index = None

	def switchList(self):
		if self.running_view:
			if self.somethingRunning():
				self.running_view = False
				self["key_yellow"].setText("View running")
				self.updateEntryList()
		else:
			if not self.somethingRunning():
				return
			self.running_view = True
			self["key_yellow"].setText("View all")
			self.updateEntryList()
			self.selectionChanged()

	def checkInstall(self):
		self.checkServiceListStatus([self.installpkg])
		if self.installpkg['status']:
			text = _("Package %s installed.") % self.installpkg['name']
			self["status"].setText(text)
			message = self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=4)
			message.setTitle(_("Package installer"))
			self.checkServiceListStatus(self.serviceList)
			self.getPkgInfo()
			self.updateServiceListState()
		else:
			text = _("Could not install %s package...") % self.installpkg['name']
			self["status"].setText(text)
			message = self.session.open(MessageBox, text, MessageBox.TYPE_ERROR, timeout=4)
			message.setTitle(_("Package installer"))

	def installFinished(self, data):
		if data:
			self.msg.close()

	def installConfirm(self, confirmed):
		if confirmed:
			text = _("Installling %s...") % self.installpkg['name']
			self["status"].setText(text)
			self.msg = self.session.openWithCallback(self.checkInstall, MessageBox, text, MessageBox.TYPE_INFO, enable_input=False)
			self.msg.setTitle(_("Package installer"))
			self.sc.runCmd("opkg install %s" % self["list"].getCurrent()[5]['package'], self.installFinished)

	def selectService(self):
		current = self["list"].getCurrent()[5]
		if current is not None:
			self.index = self["list"].getIndex()
			if not current['status']:
				self.installpkg = current
				self.session.openWithCallback(self.installConfirm, MessageBox, _("Do you want to install %s package?") % current['name'], MessageBox.TYPE_YESNO, default = False)
				return
			self.curstate = current['state']
			self.session.openWithCallback(self.stateCallback, ServiceControlPanel, current)

	def stateCallback(self, state):
		if self.curstate == state:
			return
		self["list"].getCurrent()[5]['state'] = state
		self.updateEntryList()

	def pluginsetup(self):
		self.session.open(ServiceCenterSetup)

plugin_name = "Service Manager"
plugin_description = "System services control center"

def pluginmenu(session,**kwargs):
    session.open(ServiceCenter)

def extensionsmenu(session, **kwargs):
	pluginmenu(session, **kwargs)

def setupmenu(menuid):
	if menuid == "setup":
		return [(plugin_name, pluginmenu, "service_manager", 50)]
	return [ ]

extDescriptor = PluginDescriptor(name = plugin_name, description = plugin_description, where = PluginDescriptor.WHERE_EXTENSIONSMENU, fnc = extensionsmenu)
menuDescriptor = PluginDescriptor(name = plugin_name, description = plugin_description, where = PluginDescriptor.WHERE_MENU, fnc = setupmenu)

def Plugins(**kwargs):
	result = [
        PluginDescriptor(
            name=plugin_name,
            description = plugin_description,
            where = PluginDescriptor.WHERE_PLUGINMENU,
            fnc = pluginmenu
        )]

	if config.plugins.servicemanager.onExtensionsMenu.value:
		result.append(extDescriptor)
	if config.plugins.servicemanager.onSetupMenu.value:
		result.append(menuDescriptor)
	return result

