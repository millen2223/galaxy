<template>
    <ConfigProvider v-slot="{ config }">
        <CurrentUser v-slot="{ user }">
            <UserHistories v-if="user" :user="user" v-slot="{ currentHistoryId }">
                <div v-if="currentHistoryId">
                    <b-alert :show="messageShow" :variant="messageVariant" v-html="messageText" />
                    <LoadingSpan v-if="showLoading" message="Loading Tool" />
                    <div v-if="showEntryPoints">
                        <ToolEntryPoints v-for="job in entryPoints" :job-id="job.id" :key="job.id" />
                    </div>
                    <ToolSuccess
                        v-if="showSuccess"
                        :job-def="jobDef"
                        :job-response="jobResponse"
                        :tool-name="toolName"
                    />
                    <Webhook v-if="showSuccess" type="tool" :tool-id="jobDef.tool_id" />
                    <b-modal v-model="showError" size="sm" :title="errorTitle | l" scrollable ok-only>
                        <b-alert show variant="danger">
                            The server could not complete this request. Please verify your parameter settings, retry
                            submission and contact the Galaxy Team if this error persists. A transcript of the submitted
                            data is shown below.
                        </b-alert>
                        <small class="text-muted">
                            <pre>{{ errorContentPretty }}</pre>
                        </small>
                    </b-modal>
                    <ToolRecommendation v-if="showRecommendation" :tool-id="formConfig.id" />
                    <ToolCard
                        v-if="showForm"
                        :id="formConfig.id"
                        :user="user"
                        :version="formConfig.version"
                        :title="formConfig.name"
                        :description="formConfig.description"
                        :options="formConfig"
                        :message-text="messageText"
                        :message-variant="messageVariant"
                        :disabled="disabled || showExecuting"
                        @onChangeVersion="onChangeVersion"
                        @onUpdateFavorites="onUpdateFavorites"
                        itemscope="itemscope"
                        itemtype="https://schema.org/CreativeWork"
                    >
                        <template v-slot:body>
                            <FormDisplay
                                :id="formConfig.id"
                                :inputs="formConfig.inputs"
                                :validation-scroll-to="validationScrollTo"
                                @onChange="onChange"
                                @onValidation="onValidation"
                            />
                            <FormElement
                                v-if="emailAllowed(config, user)"
                                id="send_email_notification"
                                title="Email notification"
                                help="Send an email notification when the job completes."
                                type="boolean"
                                v-model="useEmail"
                            />
                            <FormElement
                                v-if="remapAllowed"
                                id="rerun_remap_job_id"
                                :title="remapTitle"
                                :help="remapHelp"
                                type="boolean"
                                v-model="useJobRemapping"
                            />
                            <FormElement
                                v-if="reuseAllowed(user)"
                                id="use_cached_job"
                                title="Attempt to re-use jobs with identical parameters?"
                                help="This may skip executing jobs that you have already run."
                                type="boolean"
                                v-model="useCachedJobs"
                            />
                        </template>
                        <template v-slot:buttons>
                            <ButtonSpinner
                                id="execute"
                                title="Execute"
                                @onClick="onExecute(config, currentHistoryId)"
                                :wait="showExecuting"
                                :tooltip="tooltip"
                            />
                        </template>
                    </ToolCard>
                </div>
            </UserHistories>
        </CurrentUser>
    </ConfigProvider>
</template>

<script>
import { getGalaxyInstance } from "app";
import { getToolFormData, updateToolFormData, submitJob } from "./services";
import { allowCachedJobs } from "./utilities";
import ToolCard from "./ToolCard";
import ButtonSpinner from "components/Common/ButtonSpinner";
import CurrentUser from "components/providers/CurrentUser";
import ConfigProvider from "components/providers/ConfigProvider";
import LoadingSpan from "components/LoadingSpan";
import FormDisplay from "components/Form/FormDisplay";
import FormElement from "components/Form/FormElement";
import ToolSuccess from "./ToolSuccess";
import UserHistories from "components/History/providers/UserHistories";
import Webhook from "components/Common/Webhook";

export default {
    components: {
        ButtonSpinner,
        CurrentUser,
        ConfigProvider,
        LoadingSpan,
        FormDisplay,
        ToolCard,
        FormElement,
        ToolSuccess,
        UserHistories,
        Webhook,
    },
    props: {
        id: {
            type: String,
            default: null,
        },
        version: {
            type: String,
            default: null,
        },
        job_id: {
            type: String,
            default: null,
        },
        history_id: {
            type: String,
            default: null,
        },
    },
    data() {
        return {
            disabled: false,
            showLoading: true,
            showForm: false,
            showEntryPoints: false,
            showRecommendation: false,
            showSuccess: false,
            showError: false,
            showExecuting: false,
            error: null,
            formConfig: {},
            formData: {},
            remapAllowed: false,
            errorTitle: null,
            errorContent: null,
            messageShow: false,
            messageVariant: "",
            messageText: "",
            useCachedJobs: false,
            useEmail: false,
            useJobRemapping: false,
            entryPoints: [],
            jobDef: {},
            jobResponse: {},
            validationInternal: null,
            validationScrollTo: null,
            validationErrors: null,
            currentVersion: this.version,
        };
    },
    created() {
        this.requestTool().then(() => {
            const Galaxy = getGalaxyInstance();
            if (Galaxy && Galaxy.currHistoryPanel) {
                console.debug(`ToolForm::created - Started listening to history changes. [${this.id}]`);
                Galaxy.currHistoryPanel.collection.on("change", this.onHistoryChange, this);
            }
        });
    },
    beforeDestroy() {
        const Galaxy = getGalaxyInstance();
        if (Galaxy && Galaxy.currHistoryPanel) {
            Galaxy.currHistoryPanel.collection.off("change", this.onHistoryChange, this);
            console.debug(`ToolForm::beforeDestroy - Stopped listening to history changes. [${this.id}]`);
        }
    },
    computed: {
        toolName() {
            return this.formConfig.name;
        },
        tooltip() {
            return `Execute: ${this.formConfig.name} (${this.formConfig.version})`;
        },
        errorContentPretty() {
            return JSON.stringify(this.errorContent, null, 4);
        },
        remapTitle() {
            if (this.remapAllowed === "job_produced_collection_elements") {
                return "Replace elements in collection?";
            } else {
                return "Resume dependencies from this job?";
            }
        },
        remapHelp() {
            if (this.remapAllowed === "job_produced_collection_elements") {
                return "The previous run of this tool failed. Use this option to replace the failed element(s) in the dataset collection that were produced during the previous tool run.";
            } else {
                return "The previous run of this tool failed and other tools were waiting for it to finish successfully. Use this option to resume those tools using the new output(s) of this tool run.";
            }
        },
    },
    methods: {
        emailAllowed(config, user) {
            return config.server_mail_configured && !user.isAnonymous;
        },
        reuseAllowed(user) {
            return allowCachedJobs(user.preferences);
        },
        onHistoryChange() {
            console.debug(`ToolForm::created - Loading history changes. [${this.id}]`);
            this.onUpdate();
        },
        onValidation(validationInternal) {
            this.validationInternal = validationInternal;
        },
        onChange(newData, refreshRequest) {
            this.formData = newData;
            if (refreshRequest) {
                this.onUpdate();
            }
        },
        onUpdate() {
            this.disabled = true;
            updateToolFormData(this.formConfig.id, this.currentVersion, this.history_id, this.formData)
                .then((data) => {
                    this.formConfig = data;
                })
                .finally(() => {
                    this.disabled = false;
                });
        },
        onChangeVersion(newVersion) {
            this.requestTool(newVersion);
        },
        onUpdateFavorites(user, newFavorites) {
            user.preferences["favorites"] = newFavorites;
        },
        requestTool(newVersion) {
            this.currentVersion = newVersion || this.currentVersion;
            this.disabled = true;
            return getToolFormData(this.id, this.currentVersion, this.job_id, this.history_id)
                .then((data) => {
                    this.formConfig = data;
                    this.remapAllowed = this.job_id && data.job_remap;
                    this.showForm = true;
                    this.messageShow = false;
                    if (newVersion) {
                        this.messageVariant = "success";
                        this.messageText = `Now you are using '${data.name}' version ${data.version}, id '${data.id}'.`;
                    }
                })
                .catch((error) => {
                    this.messageVariant = "danger";
                    this.messageText = `Loading tool ${this.id} failed: ${error}`;
                    this.messageShow = true;
                })
                .finally(() => {
                    this.disabled = false;
                    this.showLoading = false;
                });
        },
        onExecute(config, historyId) {
            if (this.validationInternal) {
                this.validationScrollTo = this.validationInternal.slice();
                return;
            }
            this.showExecuting = true;
            const Galaxy = getGalaxyInstance();
            const jobDef = {
                history_id: historyId,
                tool_id: this.formConfig.id,
                tool_version: this.formConfig.version,
                inputs: {
                    ...this.formData,
                },
            };
            if (this.useEmail) {
                jobDef.inputs["send_email_notification"] = true;
            }
            if (this.useJobRemapping) {
                jobDef.inputs["rerun_remap_job_id"] = this.job_id;
            }
            if (this.useCachedJobs) {
                jobDef.inputs["use_cached_job"] = true;
            }
            console.debug("toolForm::onExecute()", jobDef);
            submitJob(jobDef).then(
                (jobResponse) => {
                    this.showExecuting = false;
                    if (Galaxy.currHistoryPanel) {
                        Galaxy.currHistoryPanel.refreshContents();
                    }
                    if (jobResponse.produces_entry_points) {
                        this.showEntryPoints = true;
                        this.entryPoints = jobResponse.jobs;
                    }
                    const nJobs = jobResponse && jobResponse.jobs ? jobResponse.jobs.length : 0;
                    if (nJobs > 0) {
                        this.showForm = false;
                        this.showSuccess = true;
                        this.jobDef = jobDef;
                        this.jobResponse = jobResponse;
                    } else {
                        this.showError = true;
                        this.showForm = true;
                        this.errorTitle = "Job submission rejected.";
                        this.errorContent = jobResponse;
                    }
                    if ([true, "true"].includes(config.enable_tool_recommendations)) {
                        this.showRecommendation = true;
                    }
                    document.querySelector(".center-panel").scrollTop = 0;
                },
                (e) => {
                    this.showExecuting = false;
                    let genericError = true;
                    const errorData = e && e.response && e.response.data && e.response.data.err_data;
                    if (errorData) {
                        const errorEntries = Object.entries(errorData);
                        if (errorEntries.length > 0) {
                            this.validationScrollTo = errorEntries[0];
                            genericError = false;
                        }
                    }
                    if (genericError) {
                        this.showError = true;
                        this.errorTitle = "Job submission failed.";
                        this.errorContent = this.jobDef;
                    }
                }
            );
        },
    },
};
</script>
