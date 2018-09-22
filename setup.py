from distutils.core import setup

pkg = 'SystemPlugins.ServiceManager'
setup (name = 'enigma2-plugin-systemplugins-servicemanager',
	version = '1.0',
	description = 'System services control center',
	packages = [pkg],
	package_dir = {pkg: 'plugin'},
	package_data = {pkg: ['services.xml', 'icons/*.png']},
)
