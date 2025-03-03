import _ from "underscore";
import $ from "jquery";
import Backbone from "backbone";
import { getGalaxyInstance } from "app";
import LIST_ITEM from "mvc/list/list-item";
import STATES from "mvc/dataset/states";
import faIconButton from "ui/fa-icon-button";
import BASE_MVC from "mvc/base-mvc";
import _l from "utils/localization";
import { mountNametags } from "components/Nametags";
import { Toast } from "ui/toast";
import { getAppRoot } from "onload/loadConfig";

var logNamespace = "dataset";
/*==============================================================================
TODO:
    straighten out state rendering and templates used
    inaccessible/STATES.NOT_VIEWABLE is a special case
    simplify button rendering

==============================================================================*/
var _super = LIST_ITEM.ListItemView;
/** @class Read only list view for either LDDAs, HDAs, or HDADCEs.
 *      Roughly, any DatasetInstance (and not a raw Dataset).
 */
export var DatasetListItemView = _super.extend(
    /** @lends DatasetListItemView.prototype */ {
        _logNamespace: logNamespace,

        className: `${_super.prototype.className} dataset`,
        //TODO:?? doesn't exactly match an hda's type_id
        id: function () {
            return ["dataset", this.model.get("id")].join("-");
        },

        /** Set up: instance vars, options, and event handlers */
        initialize: function (attributes) {
            if (attributes.logger) {
                this.logger = this.model.logger = attributes.logger;
            }
            this.log(`${this}.initialize:`, attributes);
            _super.prototype.initialize.call(this, attributes);

            /** where should pages from links be displayed? (default to new tab/window) */
            this.linkTarget = attributes.linkTarget || "_blank";
        },

        // mount new vue component for tags
        render: function () {
            const result = _super.prototype.render.apply(this, arguments);
            this._mountVueNametags();
            return result;
        },

        _mountVueNametags: function () {
            const container = this.$(".nametags")[0];
            const { id, model_class, tags } = this.model.attributes;
            const storeKey = `${model_class}-${id}`;
            mountNametags({ storeKey, tags }, container);
        },

        /** event listeners */
        _setUpListeners: function () {
            _super.prototype._setUpListeners.call(this);
            var self = this;

            // re-rendering on any model changes
            return self.listenTo(self.model, {
                change: function (model) {
                    // if the model moved into the ready state and is expanded without details, fetch those details now
                    if (
                        self.model.changedAttributes().state &&
                        self.model.inReadyState() &&
                        self.expanded &&
                        !self.model.hasDetails()
                    ) {
                        // normally, will render automatically (due to fetch -> change),
                        // but! setting_metadata sometimes doesn't cause any other changes besides state
                        // so, not rendering causes it to seem frozen in setting_metadata state
                        self.model.fetch({ silent: true }).done(() => {
                            self.render();
                        });
                    } else {
                        if (_.has(self.model.changed, "tags") && _.keys(self.model.changed).length === 2) {
                            // If only the tags and update time have changed,
                            // rerender specifically the titlebar region.
                            // Otherwise default to the full render.
                            self._mountVueNametags();
                        } else {
                            self.render();
                        }
                    }
                },
            });
        },

        // ......................................................................... expandable
        /** In this override, only get details if in the ready state, get rerunnable if in other states.
         *  Note: fetch with no 'change' event triggering to prevent automatic rendering.
         */
        _fetchModelDetails: function () {
            var view = this;
            if (view.model.inReadyState() && !view.model.hasDetails()) {
                return view.model.fetch({ silent: true });
            }
            return $.when();
        },

        // ......................................................................... removal
        /** Remove this view's html from the DOM and remove all event listeners.
         *  @param {Number or String} speed jq effect speed
         *  @param {Function} callback      an optional function called when removal is done (scoped to this view)
         */
        remove: function (speed, callback) {
            var view = this;
            speed = speed || this.fxSpeed;
            this.$el.fadeOut(speed, () => {
                Backbone.View.prototype.remove.call(view);
                if (callback) {
                    callback.call(view);
                }
            });
        },

        // ......................................................................... rendering
        /* TODO:
        dataset states are the issue primarily making dataset rendering complex
            each state should have it's own way of displaying/set of details
            often with different actions that can be applied
        throw in deleted/purged/visible and things get complicated easily
        I've considered (a couple of times) - creating a view for each state
            - but recreating the view during an update...seems wrong
    */
        /** In this override, add the dataset state as a class for use with state-based CSS */
        _swapNewRender: function ($newRender) {
            _super.prototype._swapNewRender.call(this, $newRender);
            if (this.model.has("state")) {
                this.$el.addClass(`state-${this.model.get("state")}`);
                this.$el.attr("data-state", this.model.get("state"));
            }
            return this.$el;
        },

        // ................................................................................ titlebar
        /** In this override, add the dataset display button. */
        _renderPrimaryActions: function () {
            // render just the display for read-only
            return [this._renderDisplayButton()];
        },

        /** Render icon-button to display dataset data */
        _renderDisplayButton: function () {
            // don't show display if not viewable or not accessible
            var state = this.model.get("state");
            if (state === STATES.NOT_VIEWABLE || state === STATES.DISCARDED || !this.model.get("accessible")) {
                return null;
            }

            var displayBtnData = {
                target: this.linkTarget,
                classes: "display-btn",
            };

            // show a disabled display if the data's been purged
            if (this.model.get("purged")) {
                displayBtnData.disabled = true;
                displayBtnData.title = _l("Cannot display datasets removed from disk");

                // disable if still uploading
            } else if (state === STATES.UPLOAD) {
                displayBtnData.disabled = true;
                displayBtnData.title = _l("This dataset must finish uploading before it can be viewed");

                // disable if still new
            } else if (state === STATES.NEW) {
                displayBtnData.disabled = true;
                displayBtnData.title = _l("This dataset is not yet viewable");
            } else {
                displayBtnData.title = _l("View data");

                // default link for dataset
                displayBtnData.href = this.model.urls.display;

                // add frame manager option onclick event
                var self = this;
                displayBtnData.onclick = (ev) => {
                    const Galaxy = getGalaxyInstance();
                    if (Galaxy.frame && Galaxy.frame.active) {
                        // Add dataset to frames.
                        const identifier = self.model.get("element_id") || self.model.get("id");
                        Galaxy.frame.addDataset(identifier);
                        ev.preventDefault();
                    }
                };
            }
            displayBtnData.faIcon = "fa-eye";
            return faIconButton(displayBtnData);
        },

        // ......................................................................... rendering details
        /** Render the enclosing div of the hda body and, if expanded, the html in the body
         *  @returns {jQuery} rendered DOM
         */
        _renderDetails: function () {
            //TODO: generalize to be allow different details for each state

            // no access - render nothing but a message
            if (this.model.get("state") === STATES.NOT_VIEWABLE) {
                return $(this.templates.noAccess(this.model.toJSON(), this));
            }

            var $details = _super.prototype._renderDetails.call(this);
            $details.find(".actions .left").empty().append(this._renderSecondaryActions());
            $details.find(".summary").html(this._renderSummary()).prepend(this._renderDetailMessages());
            $details.find(".display-applications").html(this._renderDisplayApplications());

            this._setUpBehaviors($details);
            return $details;
        },

        /** Defer to the appropo summary rendering fn based on state */
        _renderSummary: function () {
            var json = this.model.toJSON();
            var summaryRenderFn = this.templates.summaries[json.state];
            summaryRenderFn = summaryRenderFn || this.templates.summaries.unknown;
            return summaryRenderFn(json, this);
        },

        /** Render messages to be displayed only when the details are shown */
        _renderDetailMessages: function () {
            var view = this;
            var $warnings = $('<div class="detail-messages"></div>');
            var json = view.model.toJSON();
            //TODO:! unordered (map)
            _.each(view.templates.detailMessages, (templateFn) => {
                $warnings.append($(templateFn(json, view)));
            });
            return $warnings;
        },

        /** Render the external display application links */
        _renderDisplayApplications: function () {
            if (this.model.isDeletedOrPurged()) {
                return "";
            }
            // render both old and new display apps using the same template
            return [
                this.templates.displayApplications(this.model.get("display_apps"), this),
                this.templates.displayApplications(this.model.get("display_types"), this),
            ].join("");
        },

        // ......................................................................... secondary/details actions
        /** A series of links/buttons for less commonly used actions: re-run, info, etc. */
        _renderSecondaryActions: function () {
            this.debug("_renderSecondaryActions");
            switch (this.model.get("state")) {
                case STATES.NOT_VIEWABLE:
                    return [];
                case STATES.OK:
                case STATES.FAILED_METADATA:
                case STATES.ERROR:
                    return [
                        this._renderDownloadButton(),
                        this._renderClipboardButton(),
                        this._renderShowParamsButton(),
                    ];
            }
            return [this._renderShowParamsButton()];
        },

        /** Render icon-button to show the input and output (stdout/err) for the job that created this.
         *  @returns {jQuery} rendered DOM
         */
        _renderShowParamsButton: function () {
            const url = `datasets/${this.model.get("id")}/details`;

            return faIconButton({
                title: _l("View details"),
                classes: "params-btn",
                href: this.model.urls.show_params,
                target: this.linkTarget,
                faIcon: "fa-info-circle",
                onclick: (ev) => {
                    const Galaxy = getGalaxyInstance();
                    if (Galaxy.frame && Galaxy.frame.active) {
                        ev.preventDefault();
                        Galaxy.frame.add({
                            url: `${getAppRoot()}${url}`,
                            title: `Dataset Details of ${this.model.get("name")}`,
                        });
                    } else if (Galaxy.router) {
                        ev.preventDefault();
                        Galaxy.router.push(url);
                        Galaxy.trigger("activate-hda", this.model.get("id"));
                    }
                },
            });
        },
        isPurged: function () {
            // don't show anything if the data's been purged
            return !!(this.model.get("purged") || !this.model.hasData());
        },
        /** Render icon-button/popupmenu to download the data (and/or the associated meta files (bai, etc.)) for this.
         *  @returns {jQuery} rendered DOM
         */
        _renderClipboardButton: function () {
            const isUnsharable =
                this.model.attributes.permissions &&
                this.model.attributes.permissions.access.length === 1 &&
                this.model.attributes.permissions.access.includes(getGalaxyInstance().user.id);

            var urls = this.model.urls;
            if (!this.isPurged() && urls.download) {
                let title = _l("Copy link");
                let style = "";
                if (isUnsharable) {
                    title = _l("Preferences restrict sharing");
                    style = "opacity: 0.3";
                }
                return faIconButton({
                    faIcon: "fa-chain",
                    style: style,
                    title: title,
                    onclick: function () {
                        if (!isUnsharable) {
                            navigator.clipboard.writeText(`${window.location.origin}${urls.download}`).then(() => {
                                Toast.info("Link is copied to your clipboard");
                            });
                        } else {
                            Toast.warning("Dataset is not sharable");
                        }
                    },
                });
            }
        },
        /** Render icon-button/popupmenu to download the data (and/or the associated meta files (bai, etc.)) for this.
         *  @returns {jQuery} rendered DOM
         */
        _renderDownloadButton: function () {
            if (this.isPurged()) {
                return null;
            }

            // return either: a popupmenu with links to download assoc. meta files (if there are meta files)
            //  or a single download icon-button (if there are no meta files)
            if (!_.isEmpty(this.model.get("meta_files"))) {
                return this._renderMetaFileDownloadButton();
            }

            return $(`
                <a class="download-btn icon-btn" href="${this.model.urls.download}" title="${_l("Download")}">
                    <span class="fa fa-floppy-o"></span>
                </a>`);
        },

        /** Render the download button which opens a dropdown with links to download assoc. meta files (indices, etc.) */
        _renderMetaFileDownloadButton: function () {
            var urls = this.model.urls;
            return $(`
                <div class="metafile-dropdown dropdown">
                    <a class="download-btn icon-btn" href="${urls.download}" data-toggle="dropdown" title="${_l(
                "Download"
            )}">
                        <span class="fa fa-floppy-o"></span>
                    </a>
                    <div class="dropdown-menu" role="menu">
                        <a class="dropdown-item" href="${urls.download}">
                            ${_l("Download dataset")}
                        </a>
                        ${_.map(
                            this.model.get("meta_files"),
                            (meta_file) =>
                                `<a class="dropdown-item" href="${urls.meta_download + meta_file.file_type}">
                                    ${_l("Download")} ${meta_file.file_type}
                                </a>`
                        )}
                    </div>
                </div>`);
        },

        // ......................................................................... misc
        events: _.extend(_.clone(_super.prototype.events), {
            "click .display-btn": function (ev) {
                this.trigger("display", this, ev);
            },
            "click .params-btn": function (ev) {
                this.trigger("params", this, ev);
            },
            "click .download-btn": function (ev) {
                this.trigger("download", this, ev);
            },
        }),

        // ......................................................................... misc
        /** String representation */
        toString: function () {
            var modelString = this.model ? `${this.model}` : "(no model)";
            return `DatasetListItemView(${modelString})`;
        },
    }
);

// ............................................................................ TEMPLATES
/** underscore templates */
DatasetListItemView.prototype.templates = (() => {
    //TODO: move to require text! plugin

    var warnings = _.extend({}, _super.prototype.templates.warnings, {
        failed_metadata: BASE_MVC.wrapTemplate([
            // failed metadata is rendered as a warning on an otherwise ok dataset view
            '<% if( model.state === "failed_metadata" ){ %>',
            '<div class="warningmessagesmall">',
            _l("An error occurred setting the metadata for this dataset"),
            "</div>",
            "<% } %>",
        ]),
        error: BASE_MVC.wrapTemplate([
            // error during index fetch - show error on dataset
            "<% if( model.error ){ %>",
            '<div class="errormessagesmall">',
            _l("There was an error getting the data for this dataset"),
            ": <%- model.error %>",
            "</div>",
            "<% } %>",
        ]),
        purged: BASE_MVC.wrapTemplate([
            "<% if( model.purged ){ %>",
            '<div class="purged-msg warningmessagesmall">',
            _l("This dataset has been deleted and removed from disk"),
            "</div>",
            "<% } %>",
        ]),
        deleted: BASE_MVC.wrapTemplate([
            // deleted not purged
            "<% if( model.deleted && !model.purged ){ %>",
            '<div class="deleted-msg warningmessagesmall">',
            _l("This dataset has been deleted"),
            "</div>",
            "<% } %>",
        ]),

        //NOTE: hidden warning is only needed for HDAs
    });

    var detailsTemplate = BASE_MVC.wrapTemplate(
        [
            '<div class="details">',
            '<div class="summary"></div>',

            '<div class="actions clear">',
            '<div class="left"></div>',
            '<div class="right"></div>',
            "</div>",

            // do not display tags, annotation, display apps, or peek when deleted
            "<% if( !dataset.deleted && !dataset.purged ){ %>",
            '<div class="tags-display"></div>',
            '<div class="annotation-display"></div>',

            '<div class="display-applications"></div>',

            "<% if( dataset.peek ){ %>",
            '<pre class="dataset-peek"><%= dataset.peek %></pre>',
            "<% } %>",
            "<% } %>",
            "</div>",
        ],
        "dataset"
    );

    var noAccessTemplate = BASE_MVC.wrapTemplate(
        [
            '<div class="details">',
            '<div class="summary">',
            _l("You do not have permission to view this dataset"),
            "</div>",
            "</div>",
        ],
        "dataset"
    );

    //TODO: still toooooooooooooo complex - rework
    var summaryTemplates = {};
    summaryTemplates[STATES.OK] = summaryTemplates[STATES.FAILED_METADATA] = BASE_MVC.wrapTemplate(
        [
            "<% if( dataset.misc_blurb ){ %>",
            '<div class="blurb">',
            '<span class="value"><%- dataset.misc_blurb %></span>',
            "</div>",
            "<% } %>",

            "<% if( dataset.file_ext ){ %>",
            '<div class="datatype">',
            '<label class="prompt">',
            _l("format"),
            "</label>",
            '<span class="value"><%- dataset.file_ext %></span>',
            "</div>",
            "<% } %>",

            "<% if( dataset.metadata_dbkey ){ %>",
            '<div class="dbkey">',
            '<label class="prompt">',
            _l("database"),
            "</label>",
            '<span class="value">',
            "<%- dataset.metadata_dbkey %>",
            "</span>",
            "</div>",
            "<% } %>",

            "<% if( dataset.misc_info ){ %>",
            '<div class="info">',
            '<span class="value"><%- dataset.misc_info %></span>',
            "</div>",
            "<% } %>",
        ],
        "dataset"
    );
    summaryTemplates[STATES.NEW] = BASE_MVC.wrapTemplate(
        ["<div>", _l("This is a new dataset and not all of its data are available yet"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.NOT_VIEWABLE] = BASE_MVC.wrapTemplate(
        ["<div>", _l("You do not have permission to view this dataset"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.DISCARDED] = BASE_MVC.wrapTemplate(
        ["<div>", _l("The job creating this dataset was cancelled before completion"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.QUEUED] = BASE_MVC.wrapTemplate(
        ["<div>", _l("This job is waiting to run"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.RUNNING] = BASE_MVC.wrapTemplate(
        ["<div>", _l("This job is currently running"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.UPLOAD] = BASE_MVC.wrapTemplate(
        ["<div>", _l("This dataset is currently uploading"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.SETTING_METADATA] = BASE_MVC.wrapTemplate(
        ["<div>", _l("Metadata is being auto-detected"), "</div>"],
        "dataset"
    );
    summaryTemplates[STATES.PAUSED] = BASE_MVC.wrapTemplate(
        [
            "<div>",
            _l('This job is paused. Use the "Resume Paused Jobs" in the history menu to resume'),
            "</div>",
            '<div class="info"><%- dataset.misc_info %></div>',
        ],
        "dataset"
    );
    summaryTemplates[STATES.ERROR] = BASE_MVC.wrapTemplate(
        [
            "<% if( !dataset.purged ){ %>",
            "<div><%- dataset.misc_blurb %></div>",
            "<% } %>",
            '<span class="help-text">',
            _l("An error occurred with this dataset"),
            ":</span>",
            '<div class="job-error-text"><%- dataset.misc_info %></div>',
        ],
        "dataset"
    );
    summaryTemplates[STATES.EMPTY] = BASE_MVC.wrapTemplate(
        ["<div>", _l("No data"), ": <i><%- dataset.misc_blurb %></i></div>"],
        "dataset"
    );
    summaryTemplates.unknown = BASE_MVC.wrapTemplate(
        ['<div>Error: unknown dataset state: "<%- dataset.state %>"</div>'],
        "dataset"
    );

    // messages to be displayed only within the details section ('below the fold')
    var detailMessageTemplates = {
        resubmitted: BASE_MVC.wrapTemplate([
            // deleted not purged
            "<% if( model.resubmitted ){ %>",
            '<div class="resubmitted-msg alert alert-info">',
            _l("The job creating this dataset has been resubmitted"),
            "</div>",
            "<% } %>",
        ]),
    };

    // this is applied to both old and new style display apps
    var displayApplicationsTemplate = BASE_MVC.wrapTemplate(
        [
            "<% _.each( apps, function( app ){ %>",
            '<div class="display-application">',
            '<span class="display-application-location"><%- app.label %></span> ',
            '<span class="display-application-links">',
            "<% _.each( app.links, function( link ){ %>",
            '<a target="<%- link.target %>" href="<%- link.href %>">',
            "<% print( _l( link.text ) ); %>",
            "</a> ",
            "<% }); %>",
            "</span>",
            "</div>",
            "<% }); %>",
        ],
        "apps"
    );

    return _.extend({}, _super.prototype.templates, {
        warnings: warnings,
        details: detailsTemplate,
        noAccess: noAccessTemplate,
        summaries: summaryTemplates,
        detailMessages: detailMessageTemplates,
        displayApplications: displayApplicationsTemplate,
    });
})();

// ============================================================================
export default {
    DatasetListItemView: DatasetListItemView,
};
