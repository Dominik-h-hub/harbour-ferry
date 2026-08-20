/*
 * Ferry - backend specific wording for the top level of a remote.
 * Seafile keeps files in "libraries", Nextcloud simply in folders. Which
 * word applies comes from the backend definition
 * (utilities/backends/*.py -> BACKEND["terms"]): "key" selects the
 * translated set below, "one"/"many" are the English fallback for a backend
 * whose term this UI does not know yet.
 *
 * Usage: Terminology { id: terms; source: <terms dict from Python> }
 *
 * SPDX-License-Identifier: Apache-2.0
 */

import QtQuick 2.0

QtObject {
    id: terminology

    // The {key, one, many} dict delivered by Python (account summary or
    // backend list). Empty until it arrives - "folder" is the neutral default.
    property var source: ({})

    readonly property string key: (source && source.key) ? source.key : "folder"

    // Full sentences per term instead of a composed "New %1": German (and
    // most other languages) inflect the article and the adjective with the
    // noun, so composition would produce broken grammar.
    function pick(libraryText, folderText, fallback) {
        if (key === "library") {
            return libraryText;
        }
        if (key === "folder") {
            return folderText;
        }
        // Unknown term: the backend's own English word where it fits,
        // the generic folder wording otherwise.
        return fallback ? fallback : folderText;
    }

    readonly property string one: pick(qsTr("Library"), qsTr("Folder"),
                                       (source && source.one) ? source.one : "")
    readonly property string many: pick(qsTr("Libraries"), qsTr("Folders"),
                                        (source && source.many) ? source.many : "")

    readonly property string createTitle: pick(qsTr("New library"),
                                               qsTr("New folder"))
    readonly property string created: pick(qsTr("Library created"),
                                           qsTr("Folder created"))
    readonly property string none: pick(qsTr("No libraries"), qsTr("No folders"))
    readonly property string createHint: pick(qsTr("Pull down to create a library"),
                                              qsTr("Pull down to create a folder"))
    readonly property string emptyAccount: pick(
        qsTr("The account has no libraries yet."),
        qsTr("The account has no folders yet."))
    readonly property string saveHint: pick(
        qsTr("Saving runs the connection test and opens a result page with the details and the libraries found."),
        qsTr("Saving runs the connection test and opens a result page with the details and the folders found."))

    // "Libraries (3)" / "Folders (3)" for the result page section header.
    function counted(count) {
        return pick(qsTr("Libraries (%1)"), qsTr("Folders (%1)"),
                    many + " (%1)").arg(count);
    }
}
