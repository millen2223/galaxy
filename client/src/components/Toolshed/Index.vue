<template>
    <div class="overflow-auto h-100 p-1" @scroll="onScroll">
        <div v-if="error" class="alert alert-danger">{{ error }}</div>
        <div v-else>
            <b-input-group class="mb-3">
                <b-input
                    id="toolshed-repo-search"
                    placeholder="Search Repositories"
                    v-model="queryInput"
                    @input="delayQuery"
                    @change="setQuery"
                    @keydown.esc="setQuery()"
                />
                <b-input-group-append v-b-tooltip.hover :title="titleClearSearch">
                    <b-btn @click="setQuery()">
                        <i class="fa fa-times" />
                    </b-btn>
                </b-input-group-append>
            </b-input-group>
            <b-form-radio-group class="mb-3" v-model="tabValue" :options="tabOptions" />
            <div v-if="tabValue">
                <SearchList :query="query" :scrolled="scrolled" @onQuery="setQuery" @onError="setError" />
            </div>
            <div v-else>
                <InstalledList :filter="queryInput" @onQuery="setQuery" />
            </div>
        </div>
    </div>
</template>
<script>
import _l from "utils/localization";
import SearchList from "./SearchList/Index.vue";
import InstalledList from "./InstalledList/Index.vue";

export default {
    components: {
        SearchList,
        InstalledList,
    },
    data() {
        return {
            queryInput: null,
            queryDelay: 1000,
            queryTimer: null,
            queryLength: 3,
            query: null,
            scrolled: false,
            loading: false,
            total: 0,
            error: null,
            tabValue: true,
            tabOptions: [
                { text: "Search All", value: true },
                { text: "Installed Only", value: false },
            ],
            titleClearSearch: _l("clear search (esc)"),
        };
    },
    watch: {
        tabValue() {
            this.setQuery("");
        },
    },
    computed: {
        queryEmpty() {
            return !this.query || this.query.length < this.queryLength;
        },
    },
    methods: {
        clearTimer() {
            if (this.queryTimer) {
                clearTimeout(this.queryTimer);
            }
        },
        delayQuery(query) {
            this.clearTimer();
            if (query) {
                this.queryTimer = setTimeout(() => {
                    this.setQuery(query);
                }, this.queryDelay);
            } else {
                this.setQuery(query);
            }
        },
        setError(error) {
            this.error = error;
        },
        setQuery(query) {
            this.clearTimer();
            this.query = this.queryInput = query;
        },
        onScroll({ target: { scrollTop, clientHeight, scrollHeight } }) {
            this.scrolled = scrollTop + clientHeight >= scrollHeight;
        },
    },
};
</script>
