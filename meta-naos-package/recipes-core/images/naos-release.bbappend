# The image generator owns compression, encryption and signing in package mode.
# Normal factory/OTA builds keep their original task graph when it is disabled.
python __anonymous() {
    if d.getVar('NAOS_PACKAGE') != '1':
        return
    final_recipes = ('naos-image', 'naos-bundle', 'naos-bootloader-bundle', 'naos-sbom')
    dependencies = (d.getVarFlag('do_build', 'depends') or '').split()
    dependencies = [task for task in dependencies if task.split(':', 1)[0] not in final_recipes]
    dependencies.append('naos-package:do_build')
    d.setVarFlag('do_build', 'depends', ' '.join(dependencies))
}
