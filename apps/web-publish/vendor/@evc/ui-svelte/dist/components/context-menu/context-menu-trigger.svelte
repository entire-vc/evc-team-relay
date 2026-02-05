<script lang="ts">
  import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';

  type Props = ContextMenuPrimitive.TriggerProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<ContextMenuPrimitive.Trigger {...restProps}>
  {@render children?.()}
</ContextMenuPrimitive.Trigger>
