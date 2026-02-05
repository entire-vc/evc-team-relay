<script lang="ts">
  import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';

  type Props = ContextMenuPrimitive.GroupProps & {
    class?: string;
  };

  let { children, ...restProps }: Props = $props();
</script>

<ContextMenuPrimitive.Group {...restProps}>
  {@render children?.()}
</ContextMenuPrimitive.Group>
