<template>
    <div
        :id="idString"
        :name="name"
        :node-label="label"
        :class="{ 'workflow-node': true, 'node-on-scroll-to': scrolledTo, 'node-highlight': highlight }"
    >
        <div class="node-header unselectable clearfix">
            <b-button
                class="node-destroy py-0 float-right"
                variant="primary"
                size="sm"
                aria-label="destroy node"
                v-b-tooltip.hover
                title="Remove"
                @click="onRemove"
            >
                <i class="fa fa-times" />
            </b-button>
            <b-button
                :id="popoverId"
                v-if="isEnabled"
                class="node-recommendations py-0 float-right"
                variant="primary"
                size="sm"
                aria-label="tool recommendations"
            >
                <i class="fa fa-arrow-right" />
            </b-button>
            <b-popover :target="popoverId" triggers="hover" placement="bottom" :show.sync="popoverShow">
                <Recommendations
                    :get-node="getNode"
                    :get-manager="getManager"
                    :datatypes-mapper="datatypesMapper"
                    @onCreate="onCreate"
                />
            </b-popover>
            <b-button
                v-if="canClone"
                class="node-clone py-0 float-right"
                variant="primary"
                size="sm"
                aria-label="clone node"
                v-b-tooltip.hover
                title="Duplicate"
                @click="onClone"
            >
                <i class="fa fa-files-o" />
            </b-button>
            <i :class="iconClass" />
            <span class="node-title">{{ title }}</span>
        </div>
        <b-alert v-if="!!errors" variant="danger" show class="node-error">
            {{ errors }}
        </b-alert>
        <div v-else class="node-body">
            <loading-span v-if="showLoading" message="Loading details" />
            <node-input
                v-for="input in inputs"
                :key="input.name"
                :input="input"
                :get-node="getNode"
                :get-manager="getManager"
                :datatypes-mapper="datatypesMapper"
                @onAdd="onAddInput"
                @onRemove="onRemoveInput"
                @onChange="onChange"
            />
            <div v-if="showRule" class="rule" />
            <node-output
                v-for="output in outputs"
                :key="output.name"
                :output="output"
                :get-node="getNode"
                :get-manager="getManager"
                @onAdd="onAddOutput"
                @onRemove="onRemoveOutput"
                @onToggle="onToggleOutput"
                @onChange="onChange"
            />
        </div>
    </div>
</template>

<script>
import Vue from "vue";
import BootstrapVue from "bootstrap-vue";
import WorkflowIcons from "components/Workflow/icons";
import LoadingSpan from "components/LoadingSpan";
import { getGalaxyInstance } from "app";
import Recommendations from "components/Workflow/Editor/Recommendations";
import NodeInput from "./NodeInput";
import NodeOutput from "./NodeOutput";
import { ActiveOutputs } from "./modules/outputs";
import { attachDragging } from "./modules/dragging";
Vue.use(BootstrapVue);

export default {
    components: {
        LoadingSpan,
        Recommendations,
        NodeInput,
        NodeOutput,
    },
    props: {
        id: {
            type: String,
            default: "",
        },
        contentId: {
            type: String,
            default: "",
        },
        name: {
            type: String,
            default: "name",
        },
        type: {
            type: String,
            default: null,
        },
        step: {
            type: Object,
            default: null,
        },
        getManager: {
            type: Function,
            default: null,
        },
        getCanvasManager: {
            type: Function,
            default: null,
        },
        datatypesMapper: {
            type: Object,
            default: null,
        },
    },
    data() {
        return {
            popoverShow: false,
            node: null,
            inputs: [],
            outputs: [],
            inputTerminals: {},
            outputTerminals: {},
            errors: null,
            label: null,
            annotation: null,
            config_form: {},
            showLoading: true,
            highlight: false,
            scrolledTo: false,
        };
    },
    mounted() {
        this.canvasManager = this.getCanvasManager();
        this.activeOutputs = new ActiveOutputs();
        this.element = this.$el;
        this.content_id = this.contentId;

        // Set initial scroll position
        const step = this.step;
        const el = this.$el;
        if (step.position) {
            el.style.top = step.position.top + "px";
            el.style.left = step.position.left + "px";
        } else {
            const p = document.getElementById("canvas-viewport");
            const o = document.getElementById("canvas-container");
            if (p && o) {
                const left = -o.offsetLeft + (p.offsetWidth - el.offsetWidth) / 2;
                const top = -o.offsetTop + (p.offsetHeight - el.offsetHeight) / 2;
                el.style.top = `${top}px`;
                el.style.left = `${left}px`;
            }
        }

        // Attach node dragging events
        attachDragging(this.$el, {
            dragstart: () => {
                this.$emit("onActivate", this);
            },
            dragend: () => {
                this.$emit("onChange");
                this.canvasManager.drawOverview();
            },
            drag: (e, d) => {
                const o = document.getElementById("canvas-container");
                const el = this.$el;
                const rect = o.getBoundingClientRect();
                const left = (d.offsetX - rect.left) / this.canvasManager.canvasZoom;
                const top = (d.offsetY - rect.top) / this.canvasManager.canvasZoom;
                el.style.left = `${left}px`;
                el.style.top = `${top}px`;
                this.onRedraw();
            },
            dragclickonly: () => {
                this.$emit("onActivate", this);
            },
        });

        // initialize node data
        this.$emit("onAdd", this);
        if (this.step._complete) {
            this.initData(this.step);
        } else {
            this.$emit("onUpdate", this);
        }
    },
    computed: {
        title() {
            return this.label || this.name;
        },
        idString() {
            return `wf-node-step-${this.id}`;
        },
        showRule() {
            return this.inputs.length > 0 && this.outputs.length > 0;
        },
        iconClass() {
            const iconType = WorkflowIcons[this.type];
            if (iconType) {
                return `icon fa fa-fw ${iconType}`;
            }
            return null;
        },
        popoverId() {
            return `popover-${this.id}`;
        },
        canClone() {
            return this.type != "subworkflow";
        },
        isEnabled() {
            return getGalaxyInstance().config.enable_tool_recommendations;
        },
        isInput() {
            return this.type == "data_input" || this.type == "data_collection_input" || this.type == "parameter_input";
        },
    },
    methods: {
        onChange() {
            this.$emit("onChange");
        },
        onAddInput(input, terminal) {
            this.inputTerminals[input.name] = terminal;
            this.onRedraw();
        },
        onRemoveInput(input) {
            delete this.inputTerminals[input.name];
            this.onRedraw();
        },
        onAddOutput(output, terminal) {
            this.outputTerminals[output.name] = terminal;
            this.onRedraw();
        },
        onRemoveOutput(output) {
            delete this.outputTerminals[output.name];
            this.onRedraw();
        },
        onToggleOutput(name) {
            this.activeOutputs.toggle(name);
            this.$emit("onChange");
        },
        onCreate(contentId, name) {
            this.$emit("onCreate", contentId, name);
            this.popoverShow = false;
        },
        onClone() {
            this.$emit("onClone", this);
        },
        onRemove() {
            Object.values(this.inputTerminals).forEach((t) => {
                t.destroy();
            });
            Object.values(this.outputTerminals).forEach((t) => {
                t.destroy();
            });
            this.activeOutputs.filterOutputs([]);
            this.$emit("onRemove", this);
        },
        onRedraw() {
            Object.values(this.inputTerminals).forEach((t) => {
                t.redraw();
            });
            Object.values(this.outputTerminals).forEach((t) => {
                t.redraw();
            });
        },
        getNode() {
            return this;
        },
        setNode(data) {
            data.workflow_outputs = data.outputs.map((o) => {
                return {
                    output_name: o.name,
                    label: o.label,
                };
            });
            this.initData(data);

            // emit change completion event
            this.showLoading = false;
            this.$emit("onChange");
            this.$emit("onActivate", this);
        },
        setAnnotation(annotation) {
            this.annotation = annotation;
            this.$emit("onChange");
        },
        setLabel(label) {
            this.label = label;
            this.$emit("onChange");
        },
        setData(data) {
            this.tool_state = data.tool_state;
            this.errors = data.errors;
            this.tooltip = data.tooltip || "";
            this.inputs = data.inputs ? data.inputs.slice() : [];
            this.outputs = data.outputs ? data.outputs.slice() : [];
            const outputNames = this.outputs.map((output) => output.name);
            this.activeOutputs.initialize(this.outputs, data.workflow_outputs);
            this.activeOutputs.filterOutputs(outputNames);
            this.postJobActions = data.post_job_actions || {};
            this.config_form = data.config_form;
        },
        initData(data) {
            this.uuid = data.uuid;
            this.content_id = data.config_form?.id || data.content_id;
            this.annotation = data.annotation;
            this.label = data.label;
            this.setData(data);
            this.showLoading = false;
        },
        labelOutput(outputName, label) {
            return this.activeOutputs.labelOutput(outputName, label);
        },
        onScrollTo() {
            this.scrolledTo = true;
            setTimeout(() => {
                this.scrolledTo = false;
            }, 2000);
        },
        onHighlight() {
            this.highlight = true;
        },
        onUnhighlight() {
            this.highlight = false;
        },
        makeActive() {
            this.element.classList.add("node-active");
        },
        makeInactive() {
            // Keep inactive nodes stacked from most to least recently active
            // by moving element to the end of parent's node list
            document.activeElement.blur();
            const element = this.element;
            ((p) => {
                p.removeChild(element);
                p.appendChild(element);
            })(element.parentNode);
            // Remove active class
            element.classList.remove("node-active");
        },
    },
};
</script>
