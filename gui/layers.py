# This file is part of MyPaint.
# Copyright (C) 2014 by Andrew Chadwick <a.t.chadwick@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

"""Layer manipulation GUI helper code"""


## Imports

import lib.layer
from lib.helpers import escape
from lib.observable import event

import gi
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject
from gi.repository import Pango
from gettext import gettext as _

import sys
import logging
logger = logging.getLogger(__name__)


## Module vars


#TRANSLATORS: Display name template for otherwise anonymous layers
UNNAMED_LAYER_DISPLAY_NAME_TEMPLATE = _(u"{default_name} at {path}")


#: Should the layers within hidden groups be shown specially?
DISTINGUISH_DESCENDENTS_OF_INVISIBLE_PARENTS = True


## Class defs


class RootStackTreeModelWrapper (GObject.GObject, Gtk.TreeDragSource,
                                 Gtk.TreeDragDest, Gtk.TreeModel):
    """Tree model wrapper presenting a document model's layers stack

    Together with the layers panel (defined in `gui.layerswindow`),
    and `RootStackTreeView`, this forms part of the presentation logic
    for the layer stack.

    """

    ## Class vars

    INVALID_STAMP = 0
    MIN_VALID_STAMP = 1
    COLUMN_TYPES = (object,)
    LAYER_COLUMN = 0

    ## Setup

    def __init__(self, docmodel):
        """Initialize, presenting the root stack of a document model

        :param lib.document.Document docmodel: model to present
        """
        super(RootStackTreeModelWrapper, self).__init__()
        self._docmodel = docmodel
        root = docmodel.layer_stack
        self._root = root
        self._iter_stamp = 1
        self._iter_id2path = {}  # {pathid: pathtuple}
        self._iter_path2id = {}   # {pathtuple: pathid}
        root.layer_properties_changed += self._layer_props_changed_cb
        root.layer_inserted += self._layer_inserted_cb
        root.layer_deleted += self._layer_deleted_cb
        self._drag = None

    ## Python boilerplate

    def __repr__(self):
        nrows = len(list(self._root.deepiter()))
        return "<%s n=%d>" % (self.__class__.__name__, nrows)

    ## Event and update handling

    def _layer_props_changed_cb(self, root, layerpath, layer, changed):
        """Updates the display after a layer's properties change"""
        treepath = Gtk.TreePath(layerpath)
        it = self.get_iter(treepath)
        self.row_changed(treepath, it)

    def _layer_inserted_cb(self, root, path):
        """Updates the display after a layer is added"""
        self.invalidate_iters()
        it = self.get_iter(path)
        self.row_inserted(Gtk.TreePath(path), it)
        parent_path = path[:-1]
        if not parent_path:
            return
        parent = self._root.deepget(parent_path)
        if len(parent) == 1:
            parent_it = self.get_iter(parent_path)
            self.row_has_child_toggled(Gtk.TreePath(parent_path),
                                       parent_it)

    def _layer_deleted_cb(self, root, path):
        """Updates the display after a layer is removed"""
        self.invalidate_iters()
        self.row_deleted(Gtk.TreePath(path))
        parent_path = path[:-1]
        if not parent_path:
            return
        parent = self._root.deepget(parent_path)
        if len(parent) == 0:
            parent_it = self.get_iter(parent_path)
            self.row_has_child_toggled(Gtk.TreePath(parent_path),
                                       parent_it)

    def _row_dragged(self, src_path, dst_path):
        """Handles the user dragging a row to a new location"""
        self._docmodel.restack_layer(src_path, dst_path)

    ## Iterator management

    def invalidate_iters(self):
        """Invalidates all iters produced by this model"""
        # No need to zap the lookup tables: tree paths have a tightly
        # controlled vocabulary.
        if self._iter_stamp == sys.maxint:
            self._iter_stamp = self.MIN_VALID_STAMP
        else:
            self._iter_stamp += 1

    def iter_is_valid(self, it):
        """True if an iterator produced by this model is valid"""
        return it.stamp == self._iter_stamp

    @classmethod
    def _invalidate_iter(cls, it):
        """Invalidates an interator"""
        it.stamp = cls.INVALID_STAMP
        it.user_data = None

    def _get_iter_path(self, it):
        """Gets an iterator's path: None if invalid"""
        if not self.iter_is_valid(it):
            return None
        else:
            path = self._iter_id2path.get(it.user_data)
            return tuple(path)

    def _set_iter_path(self, it, path):
        """Sets an iterator's path, invalidating it if path=None"""
        if path is None:
            self._invalidate_iter(it)
        else:
            it.stamp = self._iter_stamp
            pathid = self._iter_path2id.get(path)
            if pathid is None:
                path = tuple(path)
                pathid = id(path)
                self._iter_path2id[path] = pathid
                self._iter_id2path[pathid] = path
            it.user_data = pathid

    def _create_iter(self, path):
        """Creates an iterator for the given path

        The returned pair can be returned by the ``do_*()`` virtual
        function implementations. Use this method in preference to the
        regular `Gtk.TreeIter` constructor.
        """
        if not path:
            return (False, None)
        else:
            it = Gtk.TreeIter()
            self._set_iter_path(it, tuple(path))
            return (True, it)

    def _iter_bump(self, it, delta):
        """Move an iter at its current level"""
        path = self._get_iter_path(it)
        if path is not None:
            path = list(path)
            path[-1] += delta
            path = tuple(path)
        if self.get_layer(treepath=path) is None:
            self._invalidate_iter(it)
            return False
        else:
            self._set_iter_path(it, path)
            return True

    ## Data lookup

    def get_layer(self, treepath=None, it=None):
        """Look up a layer using paths or iterators"""
        if treepath is None:
            if it is not None:
                treepath = self._get_iter_path(it)
        if treepath is None:
            return None
        if isinstance(treepath, Gtk.TreePath):
            treepath = tuple(treepath.get_indices())
        return self._root.deepget(treepath)

    ## GtkTreeModel vfunc implementation

    def do_get_flags(self):
        """Fetches GtkTreeModel flags"""
        return 0

    def do_get_n_columns(self):
        """Count of GtkTreeModel colums"""
        return len(self.COLUMN_TYPES)

    def do_get_column_type(self, n):
        return self.COLUMN_TYPES[n]

    def do_get_iter(self, treepath):
        """New iterator pointing at a node identified by GtkTreePath"""
        if not self.get_layer(treepath=treepath):
            treepath = None
        return self._create_iter(treepath)

    def do_get_path(self, it):
        """New GtkTreePath for a treeiter"""
        path = self._get_iter_path(it)
        if path is None:
            return None
        else:
            return Gtk.TreePath(path)

    def do_get_value(self, it, column):
        """Value at a particular row-iterator and column index"""
        if column != 0:
            return None
        return self.get_layer(it=it)

    def do_iter_next(self, it):
        """Move an iterator to the node after it, returning success"""
        return self._iter_bump(it, 1)

    def do_iter_previous(self, it):
        """Move an iterator to the node before it, returning success"""
        return self._iter_bump(it, -1)

    def do_iter_children(self, parent):
        """Fetch an iterator pointing at the first child of a parent"""
        return self.do_iter_nth_child(parent, 0)

    def do_iter_has_child(self, it):
        """True if an iterator has children"""
        layer = self.get_layer(it=it)
        return isinstance(layer, lib.layer.LayerStack) and len(layer) > 0

    def do_iter_n_children(self, it):
        """Count of the children of a given iterator"""
        layer = self.get_layer(it=it)
        if not isinstance(layer, lib.layer.LayerStack):
            return 0
        else:
            return len(layer)

    def do_iter_nth_child(self, it, n):
        """Fetch a specific child iterator of a parent iter"""
        if it is None:
            path = (n,)
        else:
            path = self._get_iter_path(it)
            if path is not None:
                path = list(self._get_iter_path(it))
                path.append(n)
                path = tuple(path)
        if not self.get_layer(treepath=path):
            path = None
        return self._create_iter(path)

    def do_iter_parent(self, it):
        """Fetches the parent of a valid iterator"""
        if it is None:
            parent_path = None
        else:
            path = self._get_iter_path(it)
            if path is None:
                parent_path = None
            else:
                parent_path = list(path)
                parent_path.pop(-1)
                parent_path = tuple(parent_path)
            if parent_path == ():
                parent_path = None
        return self._create_iter(parent_path)

    ## GtkTreeDragSourceIface vfunc implementation

    def do_row_draggable(self, path):
        """Checks whether a row can be dragged"""
        path = tuple(path)
        return self._root.deepget(path) is not None

    def do_drag_data_get(self, path, selection_data):
        """Extracts source row data for a view's active drag"""
        # HACK: fill in the GtkSelectionData so that the drag protocol
        # can proceed.  Need atomicity/undoability though, so fill in
        # details during the protocol exchange.
        Gtk.tree_set_row_drag_data(selection_data, self, path)
        self._drag = {
            "src": tuple(path),
            "targ": None,
        }
        return True

    def do_drag_data_delete(self, path):
        """Final deletion stage in the high-level DnD protocol"""
        del_path = tuple(path)
        if self._drag is None:
            return False
        src_path = self._drag.get("src")
        targ_path = self._drag.get("targ")
        self._drag = None
        if del_path != src_path:
            return False
        self._row_dragged(src_path, targ_path)

    ## GtkTreeDragDestIface vfunc implementation

    def do_row_drop_possible(self, path, selection_data):
        """Checks whether a row can be dragged"""
        if self._drag is None:
            return False
        src_path = self._drag.get("src")
        path = tuple(path)
        # By default, forbid dragging into a target path which doesn't
        # exist as a means of creating layer groups.
        if not self.allow_drag_into:
            target_layer = self._root.deepget(path)
            if target_layer is None:
                target_parent = self._root.deepget(path[:-1])
                if not isinstance(target_parent, lib.layer.LayerStack):
                    return False
        # Can't move a path under itself
        return not lib.layer.path_startswith(path, src_path)

    def do_drag_data_received(self, path, selection_data):
        """Receives data at the drop phase of the DnD proto"""
        # gtk_tree_get_row_drag_data turns out to be quite buggy in GTK
        # 3.12, often screwing up the view's idea of tree even when it's
        # not changed. Another reason to build up details as we go.
        if self._drag is None:
            return False
        self._drag["targ"] = tuple(path)
        return True


class RootStackTreeView (Gtk.TreeView):
    """GtkTreeView tailored for a doc's root layer stack"""

    def __init__(self, docmodel):
        super(RootStackTreeView, self).__init__()
        self._docmodel = docmodel

        treemodel = RootStackTreeModelWrapper(docmodel)
        self.set_model(treemodel)
        self.set_reorderable(True)

        self.connect("button-press-event", self._button_press_cb)

        # Motion and modifier keys during drag
        self.connect("drag-begin", self._drag_begin_cb)
        self.connect("drag-end", self._drag_end_cb)

        # Track updates from the model
        self._processing_model_updates = False
        root = docmodel.layer_stack
        root.current_path_updated += self._current_path_updated_cb
        root.expand_layer += self._expand_layer_cb
        root.collapse_layer += self._collapse_layer_cb
        root.layer_content_changed += self._layer_content_changed_cb
        root.current_layer_solo_changed += lambda *a: self.queue_draw()

        # View behaviour and appearance
        self.set_headers_visible(False)
        selection = self.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        self.set_size_request(100, 100)

        # Type column
        cell = Gtk.CellRendererPixbuf()
        col = Gtk.TreeViewColumn(_("Type"))
        col.pack_start(cell, expand=False)
        datafunc = layer_type_pixbuf_datafunc
        col.set_cell_data_func(cell, datafunc)
        col.set_max_width(24)
        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        self.append_column(col)
        self._type_col = col
        # Name column
        cell = Gtk.CellRendererText()
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        col = Gtk.TreeViewColumn(_("Name"))
        col.pack_start(cell, expand=True)
        datafunc = layer_name_text_datafunc
        col.set_cell_data_func(cell, datafunc)
        col.set_expand(True)
        col.set_min_width(48)
        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        self.append_column(col)
        self._name_col = col
        # Visibility column
        cell = Gtk.CellRendererPixbuf()
        col = Gtk.TreeViewColumn(_("Visible"))
        col.pack_start(cell, expand=False)
        datafunc = layer_visible_pixbuf_datafunc
        col.set_cell_data_func(cell, datafunc)
        col.set_max_width(24)
        self.append_column(col)
        self._visible_col = col
        # Locked column
        cell = Gtk.CellRendererPixbuf()
        col = Gtk.TreeViewColumn(_("Locked"))
        col.pack_start(cell, expand=False)
        datafunc = layer_locked_pixbuf_datafunc
        col.set_cell_data_func(cell, datafunc)
        col.set_max_width(24)
        self.append_column(col)
        self._locked_col = col
        # View appearance
        self.set_show_expanders(True)
        self.set_enable_tree_lines(True)
        self.set_expander_column(self._name_col)

    ## Low-level GDK event handlers

    def _button_press_cb(self, view, event):
        """Handle button presses (visibility, locked, naming)"""
        #if self._processing_model_updates:
        #    return
        # Basic details about the click
        double_click = (event.type == Gdk.EventType._2BUTTON_PRESS)
        is_menu = event.triggers_context_menu()
        # Determine which row & column was clicked
        x, y = int(event.x), int(event.y)
        bw_x, bw_y = view.convert_widget_to_bin_window_coords(x, y)
        click_info = view.get_path_at_pos(bw_x, bw_y)
        if click_info is None:
            return True
        treemodel = self.get_model()
        click_treepath, click_col, cell_x, cell_y = click_info
        layer = treemodel.get_layer(treepath=click_treepath)
        docmodel = self._docmodel
        rootstack = docmodel.layer_stack
        # Eye/visibility column toggles kinds of visibility
        if (click_col is self._visible_col) and not is_menu:
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                current_solo = rootstack.current_layer_solo
                rootstack.current_layer_solo = not current_solo
            elif rootstack.current_layer_solo:
                rootstack.current_layer_solo = False
            else:
                new_visible = not layer.visible
                docmodel.set_layer_visibility(new_visible, layer)
            return True
        # Layer lock column
        elif (click_col is self._locked_col) and not is_menu:
            new_locked = not layer.locked
            docmodel.set_layer_locked(new_locked, layer)
            return True
        # Double-clicking the name column is a request to rename
        elif (click_col is self._name_col) and not is_menu:
            if double_click:
                self.current_layer_rename_requested()
                return True
        # Click an un-selected layer row to select it
        click_layerpath = tuple(click_treepath.get_indices())
        if click_layerpath != rootstack.current_path:
            docmodel.select_layer(path=click_layerpath)
            self.current_layer_changed()
        # The type icon column acts as an extra expander.
        # Some themes' expander arrows are very small.
        if (click_col is self._type_col) and not is_menu:
            self.expand_to_path(click_treepath)
            return True
        # Context menu
        if is_menu and event.type == Gdk.EventType.BUTTON_PRESS:
            self.current_layer_menu_requested(event)
            return True
        # Default behaviours: allow expanders & drag-and-drop to work
        return False

    def _drag_begin_cb(self, view, context):
        self.drag_began()

    def _drag_end_cb(self, view, context):
        self.drag_ended()

    ## Model change tracking

    def _current_path_updated_cb(self, rootstack, layerpath):
        """Respond to the current layer changing in the doc-model"""
        self._processing_model_updates = True
        self._update_selection()
        self._processing_model_updates = False

    def _expand_layer_cb(self, rootstack, path):
        if not path:
            return
        treepath = Gtk.TreePath(path)
        self.expand_to_path(treepath)

    def _collapse_layer_cb(self, rootstack, path):
        if not path:
            return
        treepath = Gtk.TreePath(path)
        self.collapse_row(treepath)

    def _layer_content_changed_cb(self, rootstack, layer, *args):
        if not layer:
            return
        self.scroll_to_current_layer()

    def _update_selection(self):
        assert self._processing_model_updates
        sel = self.get_selection()
        root = self._docmodel.layer_stack
        layerpath = root.current_path
        if not layerpath:
            sel.unselect_all()
            return
        old_layerpath = None
        model, selected_paths = sel.get_selected_rows()
        if len(selected_paths) > 0:
            old_treepath = selected_paths[0]
            if old_treepath:
                old_layerpath = tuple(old_treepath.get_indices())
        if layerpath == old_layerpath:
            return
        sel.unselect_all()
        if len(layerpath) > 1:
            self.expand_to_path(Gtk.TreePath(layerpath[:-1]))
        if len(layerpath) > 0:
            sel.select_path(Gtk.TreePath(layerpath))
            self.scroll_to_current_layer()

    def scroll_to_current_layer(self, *_ignored):
        """Scroll to show the current layer"""
        sel = self.get_selection()
        tree_model, sel_row_paths = sel.get_selected_rows()
        if len(sel_row_paths) > 0:
            sel_row_path = sel_row_paths[0]
            if sel_row_path:
                self.scroll_to_cell(sel_row_path)

    ## Observable events (hook stuff here!)

    @event
    def current_layer_rename_requested(self):
        """Event: user double-clicked the name of the current layer"""

    @event
    def current_layer_changed(self):
        """Event: the current layer was just changed by clicking it"""

    @event
    def current_layer_menu_requested(self, gdkevent):
        """Event: user invoked the menu action over the current layer"""

    @event
    def drag_began(self):
        """Event: a drag has just started"""

    @event
    def drag_ended(self):
        """Event: a drag has just ended"""


## Helpers for views

def layer_name_text_datafunc(column, cell, model, it, data):
    """Show the layer name, with italics for layer groups"""
    layer = model.get_layer(it=it)
    if layer is None or layer.name is None:
        if layer is None:
            # Can happen under some rare conditions, code has to be
            # robust. Pick something placeholdery, and hope it's
            # temporary.
            default_name = lib.layer.PlaceholderLayer.DEFAULT_NAME
        else:
            default_name = layer.DEFAULT_NAME
        path = model.get_path(it)
        markup = UNNAMED_LAYER_DISPLAY_NAME_TEMPLATE.format(
            default_name=default_name,
            path=str(path),
        )
    else:
        markup = escape(layer.name)
    if isinstance(layer, lib.layer.LayerStack):
        markup = u"<i>%s</i>" % (markup,)
    attrs = Pango.AttrList()
    parse_result = Pango.parse_markup(markup, -1, '\000')
    parse_ok, attrs, text, accel_char = parse_result
    assert parse_ok
    cell.set_property("attributes", attrs)
    cell.set_property("text", text)


def layer_visible_pixbuf_datafunc(column, cell, model, it, data):
    """Use an open/closed eye icon to show layer visibilities"""
    layer = model.get_layer(it=it)
    rootstack = model._root
    visible = True
    greyed_out = True
    if layer:
        # Layer visibility is based on the layer's natural hidden/
        # visible flag, but the layer stack can override that.
        visible = layer.visible
        greyed_out = False
        if rootstack.current_layer_solo:
            visible = (layer is rootstack.current)
            greyed_out = True
        elif DISTINGUISH_DESCENDENTS_OF_INVISIBLE_PARENTS:
            path = model.get_path(it).get_indices()
            path.pop()
            while len(path) > 0:
                ancestor = model.get_layer(treepath=path)
                if not ancestor.visible:
                    greyed_out = True
                    break
                path.pop()
    # Pick icon
    icon_name_template = "mypaint-object{vis}{sens}-symbolic"
    icon_name = icon_name_template.format(
        vis=("-visible" if visible else "-hidden"),
        sens=("-insensitive" if greyed_out else ""),
    )
    cell.set_property("icon-name", icon_name)


def layer_locked_pixbuf_datafunc(column, cell, model, it, data):
    """Use a padlock icon to show layer immutability statuses"""
    layer = model.get_layer(it=it)
    if layer and layer.locked:
        icon_name = "mypaint-object-locked-symbolic"
    else:
        icon_name = "mypaint-object-unlocked-symbolic"
    cell.set_property("icon-name", icon_name)


def layer_type_pixbuf_datafunc(column, cell, model, it, data):
    """Use the layer's icon to show its type"""
    layer = model.get_layer(it=it)
    icon_name = None
    if layer is not None:
        icon_name = layer.get_icon_name()
    cell.set_property("icon-name", icon_name)


## Testing


def _test():
    """Test the custom model in an ad-hoc GUI window"""
    from lib.document import Document
    from lib.layer import PaintingLayer, LayerStack
    doc_model = Document()
    root = doc_model.layer_stack
    root.clear()
    layer_info = [
        ((0,), LayerStack(name="Layer 0")),
        ((0, 0), PaintingLayer(name="Layer 0:0")),
        ((0, 1), PaintingLayer(name="Layer 0:1")),
        ((0, 2), LayerStack(name="Layer 0:2")),
        ((0, 2, 0), PaintingLayer(name="Layer 0:2:0")),
        ((0, 2, 1), PaintingLayer(name="Layer 0:2:1")),
        ((0, 3), PaintingLayer(name="Layer 0:3")),
        ((1,), LayerStack(name="Layer 1")),
        ((1, 0), PaintingLayer(name="Layer 1:0")),
        ((1, 1), PaintingLayer(name="Layer 1:1")),
        ((1, 2), LayerStack(name="Layer 1:2")),
        ((1, 2, 0), PaintingLayer(name="Layer 1:2:0")),
        ((1, 2, 1), PaintingLayer(name="Layer 1:2:1")),
        ((1, 2, 2), PaintingLayer(name="Layer 1:2:2")),
        ((1, 2, 3), PaintingLayer(name="Layer 1:2:3")),
        ((1, 3), PaintingLayer(name="Layer 1:3")),
        ((1, 4), PaintingLayer(name="Layer 1:4")),
        ((1, 5), PaintingLayer(name="Layer 1:5")),
        ((1, 6), PaintingLayer(name="Layer 1:6")),
        ((2,), PaintingLayer(name="Layer 2")),
        ((3,), PaintingLayer(name="Layer 3")),
        ((4,), PaintingLayer(name="Layer 4")),
        ((5,), PaintingLayer(name="Layer 5")),
        ((6,), LayerStack(name="Layer 6")),
        ((6, 0), PaintingLayer(name="Layer 6:0")),
        ((6, 1), PaintingLayer(name="Layer 6:1")),
        ((6, 2), PaintingLayer(name="Layer 6:2")),
        ((6, 3), PaintingLayer(name="Layer 6:3")),
        ((6, 4), PaintingLayer(name="Layer 6:4")),
        ((6, 5), PaintingLayer(name="Layer 6:5")),
        ((7,), PaintingLayer(name="Layer 7")),
        ]
    for path, layer in layer_info:
        root.deepinsert(path, layer)
    root.set_current_path([4])

    icon_theme = Gtk.IconTheme.get_default()
    icon_theme.append_search_path("./desktop/icons")

    view = RootStackTreeView(doc_model)
    view_scroll = Gtk.ScrolledWindow()
    view_scroll.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
    scroll_pol = Gtk.PolicyType.AUTOMATIC
    view_scroll.set_policy(scroll_pol, scroll_pol)
    view_scroll.add(view)
    view_scroll.set_size_request(-1, 100)

    win = Gtk.Window()
    win.set_title(unicode(__package__))
    win.connect("destroy", Gtk.main_quit)
    win.add(view_scroll)
    win.set_default_size(300, 500)

    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    _test()
